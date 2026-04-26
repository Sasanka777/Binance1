"""
Multi-pair position sizing, exchange-filter rounding, and global kill switches.

Filters are loaded per pair. Kill switches (drawdown, daily loss, consecutive
losses) apply GLOBALLY across all pairs — if the portfolio is losing, halt
everything, not just one pair.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, client):
        self.client = client
        self._filters: dict[str, dict] = {}           # symbol -> {step_size, tick_size, min_qty, min_notional}
        self._asset_for_pair: dict[str, str] = {}     # symbol -> base asset (e.g. BTCUSDT -> BTC)
        self.equity_peak: float | None = None
        self.consecutive_losses = 0
        self.daily_loss_usdt = 0.0
        self.daily_reset_day: str | None = None

    # ----------------------------------------------------------------------
    # Exchange filters — load for every pair up front
    # ----------------------------------------------------------------------
    def load_symbol_info(self, pairs: list[str]) -> None:
        for symbol in pairs:
            info = self.client.get_symbol_info(symbol)
            if info is None:
                raise RuntimeError(f"Symbol {symbol} not found on exchange")
            f = {fl["filterType"]: fl for fl in info["filters"]}
            notional_filter = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
            min_notional = float(
                notional_filter.get("minNotional") or notional_filter.get("notional") or 10.0
            )
            self._filters[symbol] = {
                "step_size": float(f["LOT_SIZE"]["stepSize"]),
                "min_qty": float(f["LOT_SIZE"]["minQty"]),
                "tick_size": float(f["PRICE_FILTER"]["tickSize"]),
                "min_notional": min_notional,
            }
            self._asset_for_pair[symbol] = info["baseAsset"]
            log.info("[%s] filters loaded: %s", symbol, self._filters[symbol])

    def filters_for(self, symbol: str) -> dict:
        return self._filters[symbol]

    @staticmethod
    def _decimals(step: float) -> int:
        s = f"{step:.12f}".rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0

    def round_qty(self, symbol: str, qty: float) -> float:
        step = self._filters[symbol]["step_size"]
        return round(math.floor(qty / step) * step, self._decimals(step))

    def round_price(self, symbol: str, price: float) -> float:
        tick = self._filters[symbol]["tick_size"]
        return round(math.floor(price / tick) * tick, self._decimals(tick))

    # ----------------------------------------------------------------------
    # Equity — value every base asset we hold back into USDT
    # ----------------------------------------------------------------------
    def get_equity_usdt(self) -> float:
        account = self.client.get_account()
        assets_held = {b["asset"]: float(b["free"]) + float(b["locked"]) for b in account["balances"]}
        total = assets_held.get("USDT", 0.0)
        # For each traded pair, value its base asset at spot price.
        for symbol, base in self._asset_for_pair.items():
            qty = assets_held.get(base, 0.0)
            if qty <= 0:
                continue
            try:
                price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
                total += qty * price
            except Exception as e:
                log.warning("Could not price %s for equity calc: %s", base, e)
        return total

    # ----------------------------------------------------------------------
    # Position sizing — independent 1 % risk per pair
    # ----------------------------------------------------------------------
    def calc_position_size(self, symbol: str, equity: float, entry: float, stop: float) -> float:
        risk_amount = equity * config.RISK_PER_TRADE
        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            return 0.0
        raw_qty = risk_amount / stop_distance
        qty = self.round_qty(symbol, raw_qty)
        f = self._filters[symbol]
        if qty < f["min_qty"]:
            log.warning("[%s] qty %s < minQty %s", symbol, qty, f["min_qty"])
            return 0.0
        if qty * entry < f["min_notional"]:
            log.warning(
                "[%s] notional %.2f < min %.2f (equity=%.2f stop_dist=%.2f)",
                symbol, qty * entry, f["min_notional"], equity, stop_distance,
            )
            return 0.0
        return qty

    # ----------------------------------------------------------------------
    # Global kill switches
    # ----------------------------------------------------------------------
    def can_trade(self, current_equity: float) -> tuple[bool, str]:
        if self.equity_peak is None:
            self.equity_peak = current_equity
        else:
            self.equity_peak = max(self.equity_peak, current_equity)

        drawdown = (self.equity_peak - current_equity) / self.equity_peak
        if drawdown >= config.MAX_DRAWDOWN:
            return False, f"max drawdown {drawdown:.2%} >= {config.MAX_DRAWDOWN:.0%}"

        if self.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            return False, f"{self.consecutive_losses} consecutive losses (global)"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.daily_reset_day != today:
            self.daily_reset_day = today
            self.daily_loss_usdt = 0.0

        daily_loss_limit = -config.MAX_DAILY_LOSS * self.equity_peak
        if self.daily_loss_usdt <= daily_loss_limit:
            return False, f"daily loss {self.daily_loss_usdt:.2f} <= {daily_loss_limit:.2f}"

        return True, "ok"

    def record_trade_result(self, pnl_usdt: float) -> None:
        self.daily_loss_usdt += pnl_usdt
        if pnl_usdt < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
