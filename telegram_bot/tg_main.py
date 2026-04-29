"""
Telethon listener → parse signal → (paper-log | place futures trade).

Run:  python -m telegram_bot.tg_main

First-time auth: Telethon SMSes a code to TG_PHONE. Enter when prompted.
A `tg_session.session` file is then written so subsequent runs are non-interactive.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from . import tg_config as config
from . import tg_logger
from .tg_futures import FuturesClient
from .tg_parser import Signal, parse_signal

log = logging.getLogger("tg_bot")

# How often the health task runs and how long of an idle window triggers a soft
# reconnect. Telethon can silently lose its event subscription after long uptime;
# resolving the channel by ID and reconnecting fixes it.
HEALTH_CHECK_INTERVAL_SEC = 300        # 5 min
STUCK_IDLE_THRESHOLD_SEC = 1800        # 30 min with no messages → soft restart


class SignalBot:
    def __init__(self) -> None:
        if not (config.TG_API_ID and config.TG_API_HASH and config.TG_PHONE):
            raise RuntimeError(
                "Missing Telegram credentials. Get TG_API_ID + TG_API_HASH from "
                "https://my.telegram.org and add to .env along with TG_PHONE."
            )

        self.tg = TelegramClient(
            config.SESSION_NAME, config.TG_API_ID, config.TG_API_HASH
        )

        self.fc: FuturesClient | None = None
        if not config.PAPER_MODE:
            self.fc = FuturesClient(
                config.BINANCE_FUTURES_API_KEY,
                config.BINANCE_FUTURES_API_SECRET,
                testnet=config.FUTURES_TESTNET,
            )
            log.info("LIVE MODE — orders will hit Binance Futures Testnet")
        else:
            log.info("PAPER MODE — signals will be logged but no orders placed")

        self.channel_id: int | None = None     # resolved at startup
        self.last_message_time: float = time.time()

    # ------------------------------------------------------------------
    # Telegram message → Signal → action
    # ------------------------------------------------------------------
    async def on_message(self, event: events.NewMessage.Event) -> None:
        msg: Message = event.message
        text = msg.text or ""
        if not text.strip():
            return

        log.info("Message %s: %s...", msg.id, text[:80].replace("\n", " | "))

        sig = parse_signal(text)
        if not sig:
            log.debug("Not a signal — skipping")
            return

        skip = self._validate(sig, msg.date)
        common = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "msg_time": msg.date.isoformat(),
            "coin": sig.coin, "quote": sig.quote, "side": sig.side,
            "entry_type": sig.entry_type, "entry_price": sig.entry_price or "",
            "stop_loss": sig.stop_loss,
            "tps_pct": "|".join(f"{p:g}" for p in sig.take_profits),
            "leverage_min": sig.leverage_min, "leverage_max": sig.leverage_max,
        }
        if skip:
            log.warning("[%s] SKIP: %s", sig.symbol, skip)
            tg_logger.log_signal({**common, "status": "SKIPPED", "skip_reason": skip})
            return

        log.info(
            "[%s] ACCEPTED: %s SL=%s TPs=%s Lev=%d-%dx",
            sig.symbol, sig.side, sig.stop_loss, sig.take_profits,
            sig.leverage_min, sig.leverage_max,
        )
        tg_logger.log_signal({**common, "status": "ACCEPTED", "skip_reason": ""})

        if config.PAPER_MODE:
            return
        try:
            await asyncio.to_thread(self._execute, sig)
        except Exception:
            log.exception("[%s] execution failed", sig.symbol)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate(self, sig: Signal, msg_dt) -> str | None:
        # Age
        if msg_dt.tzinfo is None:
            msg_dt = msg_dt.replace(tzinfo=timezone.utc)
        age_sec = (datetime.now(timezone.utc) - msg_dt).total_seconds()
        if age_sec > config.SIGNAL_MAX_AGE_SEC:
            return f"signal too old ({age_sec:.0f}s)"
        # Quote / blacklist
        if sig.quote not in config.ALLOWED_QUOTES:
            return f"quote {sig.quote} not in allowed list"
        if sig.coin in config.BLACKLIST_BASES:
            return f"{sig.coin} on blacklist"
        # TPs
        if not sig.take_profits:
            return "no TPs parsed"
        # SL
        if sig.stop_loss <= 0:
            return "invalid SL"
        return None

    # ------------------------------------------------------------------
    # Execute (futures)
    # ------------------------------------------------------------------
    def _execute(self, sig: Signal) -> None:
        assert self.fc is not None
        symbol = sig.symbol

        # Pick which leverage the signal recommends based on config.
        # The chosen value is used both to size the position and to compute
        # TP price levels (signal author assumes that leverage when writing
        # profit % targets).
        if config.LEVERAGE_USE == "MIN":
            signal_lev = max(1, sig.leverage_min)
        elif config.LEVERAGE_USE == "MID":
            signal_lev = max(1, (sig.leverage_min + sig.leverage_max) // 2)
        else:  # "MAX" (default — use the signal's full allowed leverage)
            signal_lev = max(1, sig.leverage_max)
        our_lev = min(signal_lev, config.LEVERAGE_CAP)

        # Set leverage on this symbol
        self.fc.set_leverage(symbol, our_lev)

        # Entry price = current mark
        entry_price = self.fc.mark_price(symbol)

        # Position size = risk / SL distance, capped by max notional
        balance = self.fc.balance_usdt()
        if balance <= 0:
            log.error("[%s] zero futures balance", symbol)
            return

        sl_distance = abs(entry_price - sig.stop_loss)
        if sl_distance <= 0:
            log.error("[%s] zero SL distance", symbol)
            return

        # Position sizing — fixed-margin mode preferred when configured.
        if config.FIXED_MARGIN_USDT > 0:
            # Margin × leverage = notional. e.g. $3 × 20x = $60 position.
            notional = config.FIXED_MARGIN_USDT * our_lev
            qty = notional / entry_price
        else:
            # Risk-based: 1% of balance, capped by MAX_POSITION_USDT
            risk_amount = balance * config.RISK_PER_TRADE_PCT
            qty = risk_amount / sl_distance
            max_qty_notional = config.MAX_POSITION_USDT / entry_price
            qty = min(qty, max_qty_notional)
        qty = self.fc.round_qty(symbol, qty)

        if qty <= 0:
            log.error("[%s] qty rounded to 0 — risk too small for tick size", symbol)
            return

        log.info(
            "[%s] OPEN %s qty=%s @ ~%s lev=%dx (signal lev=%dx) SL=%s",
            symbol, sig.side, qty, entry_price, our_lev, signal_lev, sig.stop_loss,
        )

        # Open position
        order = self.fc.open_market(symbol, sig.side, qty)
        order_id = order.get("orderId")
        log.info("[%s] entry order id=%s", symbol, order_id)

        # Compute TP price levels (interpret targets as profit % at signal lev)
        tp_prices: list[float] = []
        for pct in sig.take_profits:
            price_move = pct / 100.0 / signal_lev
            tp = entry_price * (1 + price_move) if sig.side == "LONG" \
                else entry_price * (1 - price_move)
            tp_prices.append(self.fc.round_price(symbol, tp))

        # Place TP ladder
        remaining = qty
        for i, tp_price in enumerate(tp_prices):
            if i < len(tp_prices) - 1:
                weight = config.TP_WEIGHTS[i] if i < len(config.TP_WEIGHTS) else 0.2
                tp_qty = self.fc.round_qty(symbol, qty * weight)
                tp_qty = min(tp_qty, remaining)
            else:
                tp_qty = remaining  # last TP gets whatever's left
            if tp_qty <= 0:
                continue
            try:
                self.fc.place_take_profit(symbol, sig.side, tp_qty, tp_price)
                log.info("[%s] TP%d %s @ %s", symbol, i + 1, tp_qty, tp_price)
                remaining -= tp_qty
            except Exception as e:
                log.error("[%s] TP%d place failed: %s", symbol, i + 1, e)

        # Single SL for full position
        sl_price = self.fc.round_price(symbol, sig.stop_loss)
        try:
            self.fc.place_stop_loss(symbol, sig.side, qty, sl_price)
            log.info("[%s] SL %s @ %s", symbol, qty, sl_price)
        except Exception as e:
            log.error("[%s] SL place failed: %s", symbol, e)

        tg_logger.log_trade({
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol, "side": sig.side, "leverage": our_lev,
            "entry_price": entry_price, "qty": qty,
            "stop_loss": sl_price,
            "tp_prices": "|".join(f"{p:g}" for p in tp_prices),
            "main_order_id": order_id, "status": "OPENED",
        })

    # ------------------------------------------------------------------
    # Reliability — health monitor + soft reconnect
    # ------------------------------------------------------------------
    async def _resolve_channel(self) -> None:
        """Resolve channel username → numeric ID (more stable than username for events)."""
        try:
            entity = await self.tg.get_entity(config.TG_CHANNEL)
            self.channel_id = entity.id
            log.info("Resolved channel %s → id=%s", config.TG_CHANNEL, self.channel_id)
        except Exception as e:
            log.error("Could not resolve channel %s: %s — make sure you are subscribed",
                      config.TG_CHANNEL, e)
            raise

    async def _soft_reconnect(self) -> None:
        try:
            log.warning("Soft reconnect: disconnecting...")
            await self.tg.disconnect()
            await asyncio.sleep(5)
            log.warning("Soft reconnect: reconnecting...")
            await self.tg.connect()
            if not await self.tg.is_user_authorized():
                log.error("Not authorised after reconnect — session may be stale")
            else:
                # Re-resolve channel in case the entity cache was wiped
                await self._resolve_channel()
                log.info("Soft reconnect complete")
        except Exception:
            log.exception("Soft reconnect failed")

    async def _health_loop(self) -> None:
        """Background: every N seconds verify connectivity and check for stuck state."""
        while True:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL_SEC)
                # 1) Lightweight self-check
                try:
                    me = await asyncio.wait_for(self.tg.get_me(), timeout=15)
                    log.debug("Health OK — %s", me.username or me.first_name)
                except (asyncio.TimeoutError, Exception) as e:
                    log.warning("Health check failed (%s) — soft reconnect", e)
                    await self._soft_reconnect()
                    continue

                # 2) Stuck-state detection: no events for too long → likely lost subscription
                idle = time.time() - self.last_message_time
                if idle > STUCK_IDLE_THRESHOLD_SEC:
                    log.warning("No messages for %.0fs (>%.0fs) — soft reconnect to refresh subscription",
                                idle, STUCK_IDLE_THRESHOLD_SEC)
                    await self._soft_reconnect()
                    self.last_message_time = time.time()  # reset clock
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Health loop error")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    async def run(self) -> None:
        await self.tg.start(phone=config.TG_PHONE)
        me = await self.tg.get_me()
        log.info("Telegram connected as %s (id=%s)", me.username or me.first_name, me.id)

        await self._resolve_channel()

        # Catch-all handler — filter inside by chat_id (resilient to entity-cache drift)
        @self.tg.on(events.NewMessage())
        async def _handler(event):  # noqa: ARG001
            self.last_message_time = time.time()  # any inbound activity counts
            if event.chat_id != self.channel_id:
                return
            try:
                await self.on_message(event)
            except Exception:
                log.exception("handler error")

        log.info(
            "Listening to t.me/%s (id=%s) — paper_mode=%s leverage_cap=%dx margin=$%s",
            config.TG_CHANNEL, self.channel_id, config.PAPER_MODE,
            config.LEVERAGE_CAP, config.FIXED_MARGIN_USDT or "risk-based",
        )

        # Background reliability monitor
        asyncio.create_task(self._health_loop())

        await self.tg.run_until_disconnected()


def main() -> None:
    tg_logger.setup_logging()
    bot = SignalBot()
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
