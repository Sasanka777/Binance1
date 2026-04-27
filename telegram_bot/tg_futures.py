"""
Thin wrapper over python-binance's futures endpoints (Futures Testnet).

Differences from spot:
  - Uses futures_* methods (different endpoints).
  - Supports SHORT (sell-to-open) and LEVERAGE.
  - Filters live under futures_exchange_info(), not get_symbol_info().
  - Position close uses reduceOnly=True flag.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from binance.client import Client
from binance.exceptions import BinanceAPIException

log = logging.getLogger(__name__)


class FuturesClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        if not api_key or not api_secret:
            raise RuntimeError(
                "Missing futures API keys. Set BINANCE_FUTURES_API_KEY / "
                "BINANCE_FUTURES_API_SECRET in .env (futures testnet keys)."
            )
        self.client = Client(api_key, api_secret, testnet=testnet)
        self._filters: dict[str, dict] = {}

    # --------------------------------------------------------------------
    # Symbol filters / rounding
    # --------------------------------------------------------------------
    def filters(self, symbol: str) -> dict:
        if symbol in self._filters:
            return self._filters[symbol]
        info = self.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] != symbol:
                continue
            f = {fl["filterType"]: fl for fl in s["filters"]}
            min_notional_filter = f.get("MIN_NOTIONAL", {})
            self._filters[symbol] = {
                "step_size": float(f["LOT_SIZE"]["stepSize"]),
                "tick_size": float(f["PRICE_FILTER"]["tickSize"]),
                "min_qty": float(f["LOT_SIZE"]["minQty"]),
                "min_notional": float(min_notional_filter.get("notional") or 5.0),
                "quantity_precision": int(s.get("quantityPrecision", 3)),
                "price_precision": int(s.get("pricePrecision", 4)),
            }
            return self._filters[symbol]
        raise ValueError(f"{symbol} not listed on futures testnet")

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        return math.floor(value / step) * step

    def round_qty(self, symbol: str, qty: float) -> float:
        f = self.filters(symbol)
        return round(self._round_down(qty, f["step_size"]), f["quantity_precision"])

    def round_price(self, symbol: str, price: float) -> float:
        f = self.filters(symbol)
        return round(self._round_down(price, f["tick_size"]), f["price_precision"])

    # --------------------------------------------------------------------
    # Account / market data
    # --------------------------------------------------------------------
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
            log.info("[%s] leverage set to %dx", symbol, leverage)
        except BinanceAPIException as e:
            log.warning("[%s] set leverage %dx failed: %s", symbol, leverage, e)

    # --------------------------------------------------------------------
    # Orders
    # --------------------------------------------------------------------
    def open_market(self, symbol: str, side: str, qty: float) -> dict[str, Any]:
        order_side = "BUY" if side == "LONG" else "SELL"
        return self.client.futures_create_order(
            symbol=symbol,
            side=order_side,
            type="MARKET",
            quantity=qty,
        )

    def place_take_profit(
        self, symbol: str, side: str, qty: float, tp_price: float
    ) -> dict[str, Any]:
        # TP on a LONG closes by SELL above; on a SHORT closes by BUY below.
        order_side = "SELL" if side == "LONG" else "BUY"
        return self.client.futures_create_order(
            symbol=symbol,
            side=order_side,
            type="TAKE_PROFIT_MARKET",
            quantity=qty,
            stopPrice=tp_price,
            reduceOnly=True,
            workingType="MARK_PRICE",
        )

    def place_stop_loss(
        self, symbol: str, side: str, qty: float, sl_price: float
    ) -> dict[str, Any]:
        order_side = "SELL" if side == "LONG" else "BUY"
        return self.client.futures_create_order(
            symbol=symbol,
            side=order_side,
            type="STOP_MARKET",
            quantity=qty,
            stopPrice=sl_price,
            reduceOnly=True,
            workingType="MARK_PRICE",
        )

    def cancel_all(self, symbol: str) -> None:
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
        except BinanceAPIException as e:
            log.warning("[%s] cancel_all failed: %s", symbol, e)
