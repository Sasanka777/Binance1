"""
Main entry — multi-pair futures auto-bot.

  - Discover top 30 USDT-M futures pairs at startup
  - Fetch 100 candles of 15m history per pair
  - Subscribe to kline_15m WebSocket for each pair
  - On each closed candle:
        compute indicators → evaluate long/short → execute if all gates pass
  - Background thread polls open positions every 60s and marks them closed
    when the exchange's position size returns to 0 (TP or SL fired)

Risk gates: max concurrent, daily loss cap, per-pair cooldown — all enforced
by RiskManager before any order is placed.
"""
from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from binance import ThreadedWebsocketManager
from binance.client import Client

from . import fb_config as config
from . import fb_logger
from .fb_executor import FuturesExecutor
from .fb_risk import RiskManager
from .fb_strategy import check_long, check_short, compute_indicators
from .fb_universe import get_top_pairs

log = logging.getLogger("fb_bot")


class FuturesAutoBot:
    def __init__(self) -> None:
        if not (config.BINANCE_FUTURES_API_KEY and config.BINANCE_FUTURES_API_SECRET):
            raise RuntimeError(
                "Missing BINANCE_FUTURES_API_KEY / BINANCE_FUTURES_API_SECRET in .env"
            )
        self.executor = FuturesExecutor(
            config.BINANCE_FUTURES_API_KEY,
            config.BINANCE_FUTURES_API_SECRET,
            testnet=config.FUTURES_TESTNET,
        )
        self.client: Client = self.executor.client
        self.risk = RiskManager()

        self.pairs: list[str] = []
        self.candles: dict[str, pd.DataFrame] = {}
        self._candles_lock = threading.Lock()
        self.twm: Optional[ThreadedWebsocketManager] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Startup — discover universe + fetch history
    # ------------------------------------------------------------------
    def discover_pairs(self) -> None:
        self.pairs = get_top_pairs(self.client)
        if not self.pairs:
            raise RuntimeError("Pair universe empty — cannot start bot")

    def fetch_history(self) -> None:
        log.info("Fetching %d × %s candles for %d pairs...",
                 config.HISTORY_CANDLES, config.INTERVAL, len(self.pairs))
        ok = 0
        for pair in self.pairs:
            try:
                klines = self.client.futures_klines(
                    symbol=pair,
                    interval=config.INTERVAL,
                    limit=config.HISTORY_CANDLES,
                )
                klines = klines[:-1]   # drop in-progress candle
                self.candles[pair] = self._klines_to_df(klines)
                ok += 1
            except Exception as e:
                log.warning("[%s] history fetch failed: %s", pair, e)
        log.info("History loaded for %d/%d pairs", ok, len(self.pairs))

    @staticmethod
    def _klines_to_df(klines: list) -> pd.DataFrame:
        cols = ["open_time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "tbav", "tqav", "ignore"]
        df = pd.DataFrame(klines, columns=cols)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        return df.set_index("open_time")[["open", "high", "low", "close", "volume"]]

    def _append_candle(self, pair: str, k: dict) -> None:
        ts = pd.to_datetime(k["t"], unit="ms", utc=True)
        new = pd.DataFrame(
            {"open": [float(k["o"])], "high": [float(k["h"])],
             "low": [float(k["l"])], "close": [float(k["c"])],
             "volume": [float(k["v"])]},
            index=[ts],
        )
        df = self.candles.get(pair)
        if df is None:
            self.candles[pair] = new
            return
        if ts in df.index:
            df = df.drop(ts)
        df = pd.concat([df, new])
        if len(df) > config.HISTORY_CANDLES * 2:
            df = df.iloc[-config.HISTORY_CANDLES:]
        self.candles[pair] = df

    # ------------------------------------------------------------------
    # Per-candle signal evaluation + execution
    # ------------------------------------------------------------------
    def on_kline_closed(self, pair: str, k: dict) -> None:
        with self._candles_lock:
            try:
                self._append_candle(pair, k)
                df_i = compute_indicators(self.candles[pair])
                self._evaluate(pair, df_i)
            except Exception:
                log.exception("[%s] error processing closed candle", pair)

    def _evaluate(self, pair: str, df: pd.DataFrame) -> None:
        long_ok, long_reason = check_long(df)
        short_ok, short_reason = check_short(df)

        if not (long_ok or short_ok):
            return   # no signal — skip silently (would otherwise flood logs)

        side = "LONG" if long_ok else "SHORT"
        reason = long_reason if long_ok else short_reason

        last = df.iloc[-1]
        vol_ratio = ""
        if pd.notna(last.get("volume_sma")) and last["volume_sma"] > 0:
            vol_ratio = f"{last['volume'] / last['volume_sma']:.2f}"

        common = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pair": pair,
            "side": side,
            "close": f"{last['close']:.6g}",
            "rsi": f"{last['rsi']:.1f}" if pd.notna(last["rsi"]) else "",
            "atr_pct": f"{last['atr_pct']:.4%}" if pd.notna(last["atr_pct"]) else "",
            "volume_ratio": vol_ratio,
            "rolling_high": f"{last['rolling_high']:.6g}" if pd.notna(last["rolling_high"]) else "",
            "rolling_low": f"{last['rolling_low']:.6g}" if pd.notna(last["rolling_low"]) else "",
        }

        # Risk gate
        can, why = self.risk.can_open(pair)
        if not can:
            log.info("[%s] %s signal but blocked: %s", pair, side, why)
            fb_logger.log_signal({**common, "status": "BLOCKED", "skip_reason": why})
            return

        log.info("[%s] %s SIGNAL: %s", pair, side, reason)
        fb_logger.log_signal({**common, "status": "ACCEPTED", "skip_reason": ""})

        try:
            self._execute(pair, side)
        except Exception:
            log.exception("[%s] execution failed", pair)

    def _execute(self, pair: str, side: str) -> None:
        # Pre-flight: symbol valid on this endpoint?
        try:
            self.executor.filters(pair)
        except Exception as e:
            log.warning("[%s] symbol unavailable — skipping: %s", pair, e)
            return

        # Set leverage on this pair
        self.executor.set_leverage(pair, config.LEVERAGE)

        # Entry price = current mark
        entry_price = self.executor.mark_price(pair)

        # Size: fixed margin × leverage = notional, then qty = notional / price
        notional = config.FIXED_MARGIN_USDT * config.LEVERAGE
        qty = self.executor.round_qty(pair, notional / entry_price)
        if qty <= 0:
            log.warning("[%s] qty rounded to 0 (price=%s)", pair, entry_price)
            return

        # TP and SL prices from margin-percent targets
        tp_price_pct = config.TP_MARGIN_PCT / 100.0 / config.LEVERAGE
        sl_price_pct = config.SL_MARGIN_PCT / 100.0 / config.LEVERAGE
        if side == "LONG":
            tp_raw = entry_price * (1 + tp_price_pct)
            sl_raw = entry_price * (1 - sl_price_pct)
        else:  # SHORT
            tp_raw = entry_price * (1 - tp_price_pct)
            sl_raw = entry_price * (1 + sl_price_pct)
        tp_price = self.executor.round_price(pair, tp_raw)
        sl_price = self.executor.round_price(pair, sl_raw)

        log.info("[%s] OPEN %s qty=%s @ ~%s lev=%dx TP=%s SL=%s",
                 pair, side, qty, entry_price, config.LEVERAGE, tp_price, sl_price)

        # Market entry
        try:
            order = self.executor.open_market(pair, side, qty)
        except Exception as e:
            log.error("[%s] market open failed: %s", pair, e)
            return
        order_id = order.get("orderId")
        log.info("[%s] entry order id=%s", pair, order_id)

        # Place TP (full close at +30 % margin profit)
        try:
            self.executor.place_take_profit(pair, side, qty, tp_price)
            log.info("[%s] TP %s @ %s (full close)", pair, qty, tp_price)
        except Exception as e:
            log.error("[%s] TP place failed: %s", pair, e)

        # Place SL (full close at -10 % margin loss)
        try:
            self.executor.place_stop_loss(pair, side, qty, sl_price)
            log.info("[%s] SL %s @ %s", pair, qty, sl_price)
        except Exception as e:
            log.error("[%s] SL place failed: %s", pair, e)

        self.risk.mark_open(pair)

        fb_logger.log_trade({
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "symbol": pair, "side": side, "leverage": config.LEVERAGE,
            "entry_price": f"{entry_price:.6g}", "qty": qty,
            "tp_price": f"{tp_price:.6g}", "sl_price": f"{sl_price:.6g}",
            "main_order_id": order_id,
        })

    # ------------------------------------------------------------------
    # Background: poll for closed positions (TP/SL fired)
    # ------------------------------------------------------------------
    def _position_poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._stop_event.wait(config.POSITION_POLL_SECONDS)
                if self._stop_event.is_set():
                    break
                for pair in self.risk.open_pairs():
                    amt = self.executor.position_amount(pair)
                    if abs(amt) < 1e-9:
                        log.info("[%s] position closed on exchange — releasing slot", pair)
                        self.risk.mark_closed(pair, pnl_usdt=0.0)
                        # Clean up any leftover orders just in case
                        self.executor.cancel_all(pair)
            except Exception:
                log.exception("position poll error")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> None:
        self.discover_pairs()
        self.fetch_history()

        # Background position monitor
        poll_thread = threading.Thread(
            target=self._position_poll_loop, name="fb-position-poll", daemon=True
        )
        poll_thread.start()

        # WebSocket — subscribe to each pair's kline_15m stream
        self.twm = ThreadedWebsocketManager(
            api_key=config.BINANCE_FUTURES_API_KEY,
            api_secret=config.BINANCE_FUTURES_API_SECRET,
            testnet=config.FUTURES_TESTNET,
        )
        self.twm.start()

        def make_handler(p: str):
            def handler(msg: dict) -> None:
                if msg.get("e") == "error":
                    log.error("WS error on %s: %s", p, msg)
                    return
                k = msg.get("k") or (msg.get("data") or {}).get("k")
                if k and k.get("x"):
                    self.on_kline_closed(p, k)
            return handler

        subscribed = 0
        for pair in self.pairs:
            try:
                self.twm.start_kline_futures_socket(
                    callback=make_handler(pair),
                    symbol=pair.lower(),
                    interval=config.INTERVAL,
                )
                subscribed += 1
            except Exception as e:
                log.error("[%s] WS subscribe failed: %s", pair, e)

        log.info(
            "Bot running. Pairs=%d subscribed=%d Interval=%s Lev=%dx Margin=$%.2f. "
            "Ctrl-C to stop.",
            len(self.pairs), subscribed, config.INTERVAL,
            config.LEVERAGE, config.FIXED_MARGIN_USDT,
        )
        try:
            self.twm.join()
        except KeyboardInterrupt:
            log.info("Keyboard interrupt — shutting down")

    def stop(self) -> None:
        log.info("Shutting down...")
        self._stop_event.set()
        if self.twm is not None:
            try:
                self.twm.stop()
            except Exception:
                pass


def main() -> None:
    fb_logger.setup_logging(config.LOG_LEVEL)
    bot = FuturesAutoBot()

    def _sigint(signum, frame):
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)
    bot.run()


if __name__ == "__main__":
    main()
