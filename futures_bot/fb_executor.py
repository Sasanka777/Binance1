"""
Binance Futures Testnet order wrapper for the auto-bot.

Subset of the methods used by the telegram bot's tg_futures, kept here to
avoid coupling and to allow the two bots to diverge independently.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException

log = logging.getLogger(__name__)


class FuturesExecutor:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        if not (api_key and api_secret):
            raise RuntimeError(
                "Missing BINANCE_FUTURES_API_KEY / BINANCE_FUTURES_API_SECRET in .env"
            )
        self.client = Client(api_key, api_secret, testnet=testnet)
        self._filters: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Exchange filters / rounding
    # ------------------------------------------------------------------
    def filters(self, symbol: str) -> dict:
        if symbol in self._filters:
            return self._filters[symbol]
        info = self.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] != symbol:
                continue
            f = {fl["filterType"]: fl for fl in s["filters"]}
            mn = f.get("MIN_NOTIONAL", {})
            self._filters[symbol] = {
                "step_size": float(f["LOT_SIZE"]["stepSize"]),
                "tick_size": float(f["PRICE_FILTER"]["tickSize"]),
                "min_qty": float(f["LOT_SIZE"]["minQty"]),
                "min_notional": float(mn.get("notional") or 5.0),
                "quantity_precision": int(s.get("quantityPrecision", 3)),
                "price_precision": int(s.get("pricePrecision", 4)),
            }
            return self._filters[symbol]
        raise ValueError(f"{symbol} not on futures endpoint")

    @staticmethod
    def _floor(value: float, step: float) -> float:
        return math.floor(value / step) * step

    def round_qty(self, symbol: str, qty: float) -> float:
        f = self.filters(symbol)
        return round(self._floor(qty, f["step_size"]), f["quantity_precision"])

    def round_price(self, symbol: str, price: float) -> float:
        f = self.filters(symbol)
        return round(self._floor(price, f["tick_size"]), f["price_precision"])

    # ------------------------------------------------------------------
    # Account / market
    # ------------------------------------------------------------------
    def balance_usdt(self) -> float:
        for b in self.client.futures_account_balance():
            if b["asset"] == "USDT":
                return float(b["balance"])
        return 0.0

    def mark_price(self, symbol: str) -> float:
        return float(self.client.futures_mark_price(symbol=symbol)["markPrice"])

    def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except BinanceAPIException as e:
            log.warning("[%s] set leverage %dx failed: %s", symbol, leverage, e)

    def position_amount(self, symbol: str) -> float:
        """Return signed position size — positive = LONG, negative = SHORT, 0 = flat."""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for p in positions:
                if p["symbol"] == symbol:
                    return float(p["positionAmt"])
        except BinanceAPIException as e:
            log.warning("[%s] position_information failed: %s", symbol, e)
        return 0.0

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def open_market(self, symbol: str, side: str, qty: float) -> dict[str, Any]:
        order_side = "BUY" if side == "LONG" else "SELL"
        return self.client.futures_create_order(
            symbol=symbol, side=order_side, type="MARKET", quantity=qty,
        )

    def place_take_profit(
        self, symbol: str, side: str, qty: float, tp_price: float
    ) -> dict[str, Any]:
        order_side = "SELL" if side == "LONG" else "BUY"
        return self.client.futures_create_order(
            symbol=symbol, side=order_side, type="TAKE_PROFIT_MARKET",
            quantity=qty, stopPrice=tp_price, reduceOnly=True,
            workingType="MARK_PRICE",
        )

    def place_stop_loss(
        self, symbol: str, side: str, qty: float, sl_price: float
    ) -> dict[str, Any]:
        order_side = "SELL" if side == "LONG" else "BUY"
        return self.client.futures_create_order(
            symbol=symbol, side=order_side, type="STOP_MARKET",
            quantity=qty, stopPrice=sl_price, reduceOnly=True,
            workingType="MARK_PRICE",
        )

    def cancel_all(self, symbol: str) -> None:
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
        except BinanceAPIException as e:
            log.warning("[%s] cancel_all failed: %s", symbol, e)
