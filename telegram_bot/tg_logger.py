"""CSV journals for signals + executed trades."""
from __future__ import annotations

import csv
import logging
import os
import threading

from . import tg_config as config

log = logging.getLogger(__name__)

_signal_lock = threading.Lock()
_trade_lock = threading.Lock()

SIGNAL_COLUMNS = [
    "received_at", "msg_time", "coin", "quote", "side", "entry_type",
    "entry_price", "stop_loss", "tps_pct", "leverage_min", "leverage_max",
    "status", "skip_reason",
]

TRADE_COLUMNS = [
    "opened_at", "symbol", "side", "leverage", "entry_price", "qty",
    "stop_loss", "tp_prices", "main_order_id", "status",
]


def _ensure_header(path: str, columns: list[str]) -> None:
    if os.path.exists(path):
        return
    with open(path, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=columns).writeheader()


def log_signal(row: dict) -> None:
    _ensure_header(config.SIGNAL_LOG, SIGNAL_COLUMNS)
    safe = {k: row.get(k, "") for k in SIGNAL_COLUMNS}
    with _signal_lock, open(config.SIGNAL_LOG, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=SIGNAL_COLUMNS).writerow(safe)


def log_trade(row: dict) -> None:
    _ensure_header(config.TRADE_LOG, TRADE_COLUMNS)
    safe = {k: row.get(k, "") for k in TRADE_COLUMNS}
    with _trade_lock, open(config.TRADE_LOG, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=TRADE_COLUMNS).writerow(safe)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(config.APP_LOG)],
    )
