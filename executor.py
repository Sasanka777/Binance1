"""
Thin wrapper around python-binance's order endpoints — multi-pair aware.

Every call takes `symbol` as first arg. Logs include a [SYMBOL] prefix.
"""
from __future__ import annotations

import logging

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, client: Client, risk_manager):
        self.client = client
        self.rm = risk_manager

    # ----------------------------------------------------------------------
    # Market orders
    # ----------------------------------------------------------------------
    def market_buy(self, symbol: str, qty: float):
        try:
            order = self.client.create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quantity=qty,
                newOrderRespType="FULL",
            )
            fill = self._avg_fill_price(order)
            log.info("[%s] MARKET BUY filled qty=%s avg=%s", symbol, qty, fill)
            return order, fill
        except (BinanceAPIException, BinanceOrderException) as e:
            log.error("[%s] MARKET BUY failed qty=%s: %s", symbol, qty, e)
            return None, None

    def market_sell(self, symbol: str, qty: float):
        try:
            order = self.client.create_order(
                symbol=symbol,
                side="SELL",
                type="MARKET",
                quantity=qty,
                newOrderRespType="FULL",
            )
            fill = self._avg_fill_price(order)
            log.info("[%s] MARKET SELL filled qty=%s avg=%s", symbol, qty, fill)
            return order, fill
        except (BinanceAPIException, BinanceOrderException) as e:
            log.error("[%s] MARKET SELL failed qty=%s: %s", symbol, qty, e)
            return None, None

    @staticmethod
    def _avg_fill_price(order: dict) -> float:
        fills = order.get("fills", []) or []
        total_qty = sum(float(f["qty"]) for f in fills)
        if total_qty <= 0:
            return float(order.get("price") or 0.0)
        total_cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        return total_cost / total_qty

    # ----------------------------------------------------------------------
    # OCO (TP limit + SL stop) for the 50 % partial-close leg
    # ----------------------------------------------------------------------
    def oco_sell(self, symbol: str, qty: float, tp_price: float, sl_price: float):
        tp_price = self.rm.round_price(symbol, tp_price)
        sl_price = self.rm.round_price(symbol, sl_price)
        sl_limit_price = self.rm.round_price(symbol, sl_price * 0.998)
        try:
            resp = self.client.create_oco_order(
                symbol=symbol,
                side="SELL",
                quantity=qty,
                price=f"{tp_price}",
                stopPrice=f"{sl_price}",
                stopLimitPrice=f"{sl_limit_price}",
                stopLimitTimeInForce="GTC",
            )
            log.info("[%s] OCO SELL placed qty=%s TP=%s SL=%s", symbol, qty, tp_price, sl_price)
            return resp
        except (BinanceAPIException, BinanceOrderException) as e:
            log.error("[%s] OCO SELL failed qty=%s TP=%s SL=%s: %s", symbol, qty, tp_price, sl_price, e)
            return None

    # ----------------------------------------------------------------------
    # Standalone stop-loss — used for remainder leg and trailing
    # ----------------------------------------------------------------------
    def stop_loss_sell(self, symbol: str, qty: float, stop_price: float):
        stop_price = self.rm.round_price(symbol, stop_price)
        stop_limit_price = self.rm.round_price(symbol, stop_price * 0.998)
        try:
            order = self.client.create_order(
                symbol=symbol,
                side="SELL",
                type="STOP_LOSS_LIMIT",
                quantity=qty,
                price=f"{stop_limit_price}",
                stopPrice=f"{stop_price}",
                timeInForce="GTC",
            )
            log.info("[%s] STOP_LOSS_LIMIT placed qty=%s stop=%s", symbol, qty, stop_price)
            return order
        except (BinanceAPIException, BinanceOrderException) as e:
            log.error("[%s] STOP_LOSS_LIMIT failed qty=%s stop=%s: %s", symbol, qty, stop_price, e)
            return None

    # ----------------------------------------------------------------------
    # Introspection / cleanup
    # ----------------------------------------------------------------------
    def order_status(self, symbol: str, order_id):
        try:
            return self.client.get_order(symbol=symbol, orderId=order_id)
        except BinanceAPIException as e:
            log.error("[%s] get_order %s failed: %s", symbol, order_id, e)
            return None

    def cancel_order(self, symbol: str, order_id):
        try:
            return self.client.cancel_order(symbol=symbol, orderId=order_id)
        except BinanceAPIException as e:
            log.warning("[%s] cancel_order %s failed: %s", symbol, order_id, e)
            return None

    def cancel_oco(self, symbol: str, order_list_id):
        try:
            return self.client.cancel_order_list(symbol=symbol, orderListId=order_list_id)
        except BinanceAPIException as e:
            log.warning("[%s] cancel_oco %s failed: %s", symbol, order_list_id, e)
            return None

    @staticmethod
    def extract_oco_order_ids(oco_response: dict) -> tuple[int | None, int | None]:
        if not oco_response:
            return None, None
        tp_id, sl_id = None, None
        for rep in oco_response.get("orderReports", []):
            otype = rep.get("type", "")
            if otype in ("LIMIT_MAKER", "LIMIT"):
                tp_id = rep.get("orderId")
            elif "STOP" in otype:
                sl_id = rep.get("orderId")
        return tp_id, sl_id
