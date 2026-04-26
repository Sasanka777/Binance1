"""
Entry point. Multi-pair bot: one SymbolWorker per pair, shared infrastructure.

States (per worker):
  FLAT         — no position
  LONG_OPEN    — entry filled; OCO (50%) + separate stop (50%) live
  LONG_TRAIL   — TP1 hit; remainder trails via chandelier stop

Only long trades are executed (Binance SPOT testnet doesn't support shorting).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone

import pandas as pd
from binance import ThreadedWebsocketManager
from binance.client import Client

import config
from executor import Executor
from logger import TradeLogger, get_pair_logger, setup_logging
from risk_manager import RiskManager
from strategy import chandelier_stop_long, check_long_entry, compute_indicators

root_log = logging.getLogger("bot")


# ==========================================================================
# SymbolWorker — per-pair state + logic
# ==========================================================================
class SymbolWorker:
    def __init__(
        self,
        symbol: str,
        client: Client,
        rm: RiskManager,
        execr: Executor,
        journal: TradeLogger,
        manager: "MultiPairBot",
    ):
        self.symbol = symbol
        self.client = client
        self.rm = rm
        self.execr = execr
        self.journal = journal
        self.manager = manager
        self.log = get_pair_logger("bot.worker", symbol)

        self.df: pd.DataFrame | None = None
        self.state: str = "FLAT"
        self.position: dict | None = None

    # ------------------------------------------------------------------
    # Historical warmup
    # ------------------------------------------------------------------
    def fetch_history(self) -> None:
        self.log.info("Fetching %s × %s klines...", config.KLINE_LIMIT, config.INTERVAL)
        klines = self.client.get_klines(
            symbol=self.symbol, interval=config.INTERVAL, limit=config.KLINE_LIMIT,
        )
        klines = klines[:-1]  # drop currently-forming candle
        self.df = self._klines_to_df(klines)
        self.log.info("History loaded: %d closed candles, latest=%s", len(self.df), self.df.index[-1])

    @staticmethod
    def _klines_to_df(klines: list) -> pd.DataFrame:
        cols = ["open_time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "tbav", "tqav", "ignore"]
        df = pd.DataFrame(klines, columns=cols)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        return df.set_index("open_time")[["open", "high", "low", "close", "volume"]]

    def _append_candle(self, k: dict) -> None:
        ts = pd.to_datetime(k["t"], unit="ms", utc=True)
        new = pd.DataFrame(
            {"open": [float(k["o"])], "high": [float(k["h"])], "low": [float(k["l"])],
             "close": [float(k["c"])], "volume": [float(k["v"])]},
            index=[ts],
        )
        if ts in self.df.index:
            self.df = self.df.drop(ts)
        self.df = pd.concat([self.df, new])
        if len(self.df) > config.KLINE_LIMIT * 2:
            self.df = self.df.iloc[-config.KLINE_LIMIT:]

    def _24h_high(self) -> float:
        return float(self.df.tail(96)["high"].max())

    # ------------------------------------------------------------------
    # Candle dispatch — serialized by manager lock
    # ------------------------------------------------------------------
    def on_candle_close(self, k: dict) -> None:
        self.log.info(
            "Candle closed %s  O=%s H=%s L=%s C=%s V=%s",
            pd.to_datetime(k["t"], unit="ms", utc=True), k["o"], k["h"], k["l"], k["c"], k["v"],
        )
        self._append_candle(k)
        df_i = compute_indicators(self.df)

        if self.state == "FLAT":
            self._try_entry(df_i)
        elif self.state == "LONG_OPEN":
            self._manage_open(df_i)
        elif self.state == "LONG_TRAIL":
            self._manage_trail(df_i)

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------
    def _try_entry(self, df_i: pd.DataFrame) -> None:
        ok, reason = check_long_entry(df_i, self._24h_high())
        if not ok:
            self.log.info("No entry: %s", reason)
            return

        self.log.info("*** LONG ENTRY SIGNAL — %s ***", reason)

        equity = self.rm.get_equity_usdt()
        can, why = self.rm.can_trade(equity)
        if not can:
            self.log.warning("Entry blocked by risk manager: %s", why)
            return

        last = df_i.iloc[-1]
        est_entry = float(last["close"])
        atr_v = float(last["atr"])
        est_stop = est_entry - config.STOP_LOSS_ATR * atr_v

        qty = self.rm.calc_position_size(self.symbol, equity, est_entry, est_stop)
        if qty <= 0:
            self.log.warning("Position size rejected")
            return

        buy_resp, fill_price = self.execr.market_buy(self.symbol, qty)
        if buy_resp is None or not fill_price:
            return

        entry = fill_price
        stop = self.rm.round_price(self.symbol, entry - config.STOP_LOSS_ATR * atr_v)
        tp1 = self.rm.round_price(self.symbol, entry + config.TAKE_PROFIT_1_ATR * atr_v)

        half = self.rm.round_qty(self.symbol, qty * config.PARTIAL_CLOSE_RATIO)
        remainder = self.rm.round_qty(self.symbol, qty - half)
        min_notional = self.rm.filters_for(self.symbol)["min_notional"]
        use_split = (
            half > 0 and remainder > 0
            and half * entry >= min_notional
            and remainder * entry >= min_notional
        )

        if use_split:
            oco = self.execr.oco_sell(self.symbol, half, tp1, stop)
            tp_id, oco_sl_id = self.execr.extract_oco_order_ids(oco)
            rem_sl = self.execr.stop_loss_sell(self.symbol, remainder, stop)
            rem_sl_id = rem_sl.get("orderId") if rem_sl else None
        else:
            self.log.warning("Qty %s can't be split — single OCO for full", qty)
            oco = self.execr.oco_sell(self.symbol, qty, tp1, stop)
            tp_id, oco_sl_id = self.execr.extract_oco_order_ids(oco)
            half, remainder = qty, 0
            rem_sl_id = None

        trade_id = self.manager.next_trade_id()
        self.position = {
            "trade_id": trade_id,
            "side": "LONG",
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "entry_price": entry,
            "atr_at_entry": atr_v,
            "stop_loss": stop,
            "take_profit_1": tp1,
            "total_qty": qty,
            "half_qty": half,
            "remainder_qty": remainder,
            "oco_order_list_id": oco.get("orderListId") if oco else None,
            "oco_tp_id": tp_id,
            "oco_sl_id": oco_sl_id,
            "remainder_sl_id": rem_sl_id,
            "current_trail_stop": stop,
            "highest_high_since_entry": entry,
            "candles_in_trade": 0,
            "tp1_hit": False,
            "equity_before": equity,
            "partial_exit_price": None,
        }
        self.state = "LONG_OPEN"
        self.log.info("Position opened #%d: %s", trade_id, self.position)
        self.manager.save_state()

    # ------------------------------------------------------------------
    # Pre-TP1 management
    # ------------------------------------------------------------------
    def _manage_open(self, df_i: pd.DataFrame) -> None:
        pos = self.position
        pos["candles_in_trade"] += 1
        last = df_i.iloc[-1]
        pos["highest_high_since_entry"] = max(pos["highest_high_since_entry"], float(last["high"]))

        if pos["oco_tp_id"]:
            tp_status = self.execr.order_status(self.symbol, pos["oco_tp_id"])
            if tp_status and tp_status["status"] == "FILLED":
                pos["tp1_hit"] = True
                pos["partial_exit_price"] = float(tp_status.get("price") or pos["take_profit_1"])
                self.log.info("TP1 FILLED at %s — moving remainder SL to breakeven",
                              pos["partial_exit_price"])
                if pos["remainder_sl_id"]:
                    self.execr.cancel_order(self.symbol, pos["remainder_sl_id"])
                breakeven = self.rm.round_price(self.symbol, pos["entry_price"])
                new_sl = self.execr.stop_loss_sell(self.symbol, pos["remainder_qty"], breakeven)
                pos["remainder_sl_id"] = new_sl.get("orderId") if new_sl else None
                pos["current_trail_stop"] = breakeven
                self.state = "LONG_TRAIL"
                self.manager.save_state()
                return

        if self._any_stop_filled():
            self._on_full_stop_out("stop_loss")
            return

        if pos["candles_in_trade"] >= config.TIME_STOP_CANDLES:
            self.log.info("Time stop reached — flattening")
            self._force_flatten("time_stop")

    # ------------------------------------------------------------------
    # Post-TP1 (trailing) management
    # ------------------------------------------------------------------
    def _manage_trail(self, df_i: pd.DataFrame) -> None:
        pos = self.position
        pos["candles_in_trade"] += 1
        last = df_i.iloc[-1]
        pos["highest_high_since_entry"] = max(pos["highest_high_since_entry"], float(last["high"]))

        if pos["remainder_sl_id"]:
            st = self.execr.order_status(self.symbol, pos["remainder_sl_id"])
            if st and st["status"] == "FILLED":
                exit_price = float(st.get("price") or pos["current_trail_stop"])
                self._finalize_trade("trailing_stop", exit_price)
                return

        new_stop = chandelier_stop_long(pos["highest_high_since_entry"], pos["atr_at_entry"])
        new_stop = self.rm.round_price(self.symbol, new_stop)
        if new_stop > pos["current_trail_stop"]:
            self.log.info("Advancing trail %s → %s", pos["current_trail_stop"], new_stop)
            if pos["remainder_sl_id"]:
                self.execr.cancel_order(self.symbol, pos["remainder_sl_id"])
            replacement = self.execr.stop_loss_sell(self.symbol, pos["remainder_qty"], new_stop)
            pos["remainder_sl_id"] = replacement.get("orderId") if replacement else None
            pos["current_trail_stop"] = new_stop
            self.manager.save_state()

        if pos["candles_in_trade"] >= config.TIME_STOP_CANDLES:
            self.log.info("Time stop reached on remainder — flattening")
            self._force_flatten("time_stop_trail")

    # ------------------------------------------------------------------
    # Exit helpers
    # ------------------------------------------------------------------
    def _any_stop_filled(self) -> bool:
        pos = self.position
        for oid_key in ("oco_sl_id", "remainder_sl_id"):
            oid = pos.get(oid_key)
            if not oid:
                continue
            st = self.execr.order_status(self.symbol, oid)
            if st and st["status"] == "FILLED":
                return True
        return False

    def _on_full_stop_out(self, reason: str) -> None:
        pos = self.position
        if pos.get("oco_order_list_id"):
            self.execr.cancel_oco(self.symbol, pos["oco_order_list_id"])
        if pos.get("remainder_sl_id"):
            self.execr.cancel_order(self.symbol, pos["remainder_sl_id"])
        if pos["remainder_qty"] > 0:
            rem_status = self.execr.order_status(self.symbol, pos["remainder_sl_id"]) if pos["remainder_sl_id"] else None
            if not rem_status or rem_status["status"] != "FILLED":
                self.execr.market_sell(self.symbol, pos["remainder_qty"])
        self._finalize_trade(reason, pos["stop_loss"])

    def _force_flatten(self, reason: str) -> None:
        pos = self.position
        if pos.get("oco_order_list_id"):
            self.execr.cancel_oco(self.symbol, pos["oco_order_list_id"])
        if pos.get("remainder_sl_id"):
            self.execr.cancel_order(self.symbol, pos["remainder_sl_id"])
        remaining = pos["remainder_qty"] if self.state == "LONG_TRAIL" else pos["total_qty"]
        if remaining > 0:
            _, fill = self.execr.market_sell(self.symbol, remaining)
            exit_price = fill or self.rm.round_price(self.symbol, float(self.df.iloc[-1]["close"]))
        else:
            exit_price = pos.get("partial_exit_price") or pos["entry_price"]
        self._finalize_trade(reason, exit_price)

    def _finalize_trade(self, reason: str, exit_price: float) -> None:
        pos = self.position
        entry = pos["entry_price"]

        if pos["tp1_hit"]:
            tp_pnl = pos["half_qty"] * (pos["partial_exit_price"] - entry)
            rem_pnl = pos["remainder_qty"] * (exit_price - entry)
            pnl = tp_pnl + rem_pnl
            blended = (
                pos["half_qty"] * pos["partial_exit_price"]
                + pos["remainder_qty"] * exit_price
            ) / pos["total_qty"]
        else:
            pnl = pos["total_qty"] * (exit_price - entry)
            blended = exit_price

        pnl_pct = pnl / pos["equity_before"] if pos["equity_before"] else 0.0

        try:
            equity_after = self.rm.get_equity_usdt()
        except Exception:
            equity_after = pos["equity_before"] + pnl

        self.journal.log_trade({
            "trade_id": pos["trade_id"],
            "pair": self.symbol,
            "entry_time": pos["entry_time"],
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "side": pos["side"],
            "entry_price": f"{entry:.2f}",
            "exit_price": f"{blended:.2f}",
            "atr_at_entry": f"{pos['atr_at_entry']:.2f}",
            "stop_loss": f"{pos['stop_loss']:.2f}",
            "take_profit_1": f"{pos['take_profit_1']:.2f}",
            "quantity": f"{pos['total_qty']:.8f}",
            "pnl_usdt": f"{pnl:.2f}",
            "pnl_pct": f"{pnl_pct:.6f}",
            "exit_reason": reason,
            "equity_before": f"{pos['equity_before']:.2f}",
            "equity_after": f"{equity_after:.2f}",
        })
        self.rm.record_trade_result(pnl)
        self.position = None
        self.state = "FLAT"
        self.manager.save_state()

    # ------------------------------------------------------------------
    # Serialize / restore
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {"state": self.state, "position": self.position}

    def restore(self, snap: dict) -> None:
        self.state = snap.get("state", "FLAT")
        self.position = snap.get("position")


# ==========================================================================
# MultiPairBot — coordinates workers, shares infra
# ==========================================================================
class MultiPairBot:
    def __init__(self):
        if not config.API_KEY or not config.API_SECRET:
            raise RuntimeError(
                "Missing API keys. Copy .env.example to .env and fill in "
                "BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET."
            )
        self.client = Client(config.API_KEY, config.API_SECRET, testnet=config.TESTNET)
        self.rm = RiskManager(self.client)
        self.execr = Executor(self.client, self.rm)
        self.journal = TradeLogger()

        self.rm.load_symbol_info(config.PAIRS)
        self.lock = threading.Lock()   # serializes all candle-close handlers
        self._trade_counter = 0

        self.workers: dict[str, SymbolWorker] = {
            pair: SymbolWorker(pair, self.client, self.rm, self.execr, self.journal, self)
            for pair in config.PAIRS
        }
        self.twm: ThreadedWebsocketManager | None = None
        self._load_state()

    def next_trade_id(self) -> int:
        self._trade_counter += 1
        return self._trade_counter

    # ------------------------------------------------------------------
    # Callback dispatch
    # ------------------------------------------------------------------
    def _handle_ws(self, msg: dict) -> None:
        if msg.get("e") == "error":
            root_log.error("WS error: %s", msg)
            return
        k = msg.get("k")
        if not (k and k.get("x")):
            return
        sym = k.get("s") or msg.get("s")
        worker = self.workers.get(sym)
        if worker is None:
            root_log.warning("No worker for symbol %s", sym)
            return
        with self.lock:
            try:
                worker.on_candle_close(k)
            except Exception:
                worker.log.exception("Error handling candle close")

    # ------------------------------------------------------------------
    # State persistence (single file, keyed by pair)
    # ------------------------------------------------------------------
    def save_state(self) -> None:
        try:
            payload = {
                "trade_counter": self._trade_counter,
                "equity_peak": self.rm.equity_peak,
                "consecutive_losses": self.rm.consecutive_losses,
                "daily_loss_usdt": self.rm.daily_loss_usdt,
                "daily_reset_day": self.rm.daily_reset_day,
                "pairs": {sym: w.snapshot() for sym, w in self.workers.items()},
            }
            tmp = config.STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f, default=str, indent=2)
            os.replace(tmp, config.STATE_FILE)
        except Exception as e:
            root_log.warning("Could not save state: %s", e)

    def _load_state(self) -> None:
        if not os.path.exists(config.STATE_FILE):
            return
        try:
            with open(config.STATE_FILE) as f:
                s = json.load(f)
        except Exception as e:
            root_log.warning("Could not load state: %s", e)
            return

        # Old single-pair format detection: has top-level "state" instead of "pairs"
        if "pairs" not in s:
            archived = config.STATE_FILE + ".bak"
            os.replace(config.STATE_FILE, archived)
            root_log.warning("Old state.json archived to %s (schema changed)", archived)
            return

        self._trade_counter = s.get("trade_counter", 0)
        self.rm.equity_peak = s.get("equity_peak")
        self.rm.consecutive_losses = s.get("consecutive_losses", 0)
        self.rm.daily_loss_usdt = s.get("daily_loss_usdt", 0.0)
        self.rm.daily_reset_day = s.get("daily_reset_day")
        for sym, snap in s.get("pairs", {}).items():
            if sym in self.workers:
                self.workers[sym].restore(snap)
                root_log.info("[%s] state restored: %s", sym, self.workers[sym].state)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> None:
        for worker in self.workers.values():
            worker.fetch_history()

        self.twm = ThreadedWebsocketManager(
            api_key=config.API_KEY,
            api_secret=config.API_SECRET,
            testnet=config.TESTNET,
        )
        self.twm.start()

        for pair in config.PAIRS:
            self.twm.start_kline_socket(
                callback=self._handle_ws,
                symbol=pair.lower(),
                interval=config.INTERVAL,
            )
            root_log.info("Subscribed to %s@kline_%s", pair.lower(), config.INTERVAL)

        root_log.info("Bot running. Pairs=%s Interval=%s Testnet=%s. Ctrl-C to stop.",
                      config.PAIRS, config.INTERVAL, config.TESTNET)
        self.twm.join()

    def stop(self) -> None:
        root_log.info("Shutting down...")
        if self.twm is not None:
            try:
                self.twm.stop()
            except Exception:
                pass


# ==========================================================================
def main() -> None:
    setup_logging(config.LOG_LEVEL)
    bot = MultiPairBot()

    def _sigint(signum, frame):
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)
    bot.run()


if __name__ == "__main__":
    main()
