"""CSV trade journal + standard application logging setup."""
from __future__ import annotations

import csv
import logging
import os

import config

log = logging.getLogger(__name__)

TRADE_COLUMNS = [
    "trade_id",
    "pair",
    "entry_time",
    "exit_time",
    "side",
    "entry_price",
    "exit_price",
    "atr_at_entry",
    "stop_loss",
    "take_profit_1",
    "quantity",
    "pnl_usdt",
    "pnl_pct",
    "exit_reason",
    "equity_before",
    "equity_after",
]


class TradeLogger:
    """Thread-safe CSV writer. Same file shared by all pairs — `pair` column identifies."""

    def __init__(self, filepath: str | None = None):
        self.filepath = filepath or config.LOG_FILE
        import threading
        self._lock = threading.Lock()
        self._ensure_header()

    def _ensure_header(self) -> None:
        needs_header = True
        if os.path.exists(self.filepath):
            with open(self.filepath, newline="") as f:
                first = f.readline().strip()
            if first:
                existing_cols = [c.strip() for c in first.split(",")]
                if existing_cols == TRADE_COLUMNS:
                    needs_header = False
                else:
                    # Old single-pair format — archive it, start fresh.
                    archived = self.filepath + ".bak"
                    os.replace(self.filepath, archived)
                    log.warning("Old trade CSV archived to %s (schema changed for multi-pair)", archived)
        if needs_header:
            with open(self.filepath, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=TRADE_COLUMNS).writeheader()

    def log_trade(self, trade: dict) -> None:
        row = {k: trade.get(k, "") for k in TRADE_COLUMNS}
        with self._lock:
            with open(self.filepath, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=TRADE_COLUMNS).writerow(row)
        log.info(
            "[%s] TRADE #%s %s entry=%s exit=%s pnl=%.2f (%.2f%%) reason=%s",
            row["pair"], row["trade_id"], row["side"], row["entry_price"], row["exit_price"],
            float(row["pnl_usdt"] or 0), float(row["pnl_pct"] or 0) * 100, row["exit_reason"],
        )


class PairLoggerAdapter(logging.LoggerAdapter):
    """Prefixes every message with [PAIR] so the shared bot.log is scannable."""

    def process(self, msg, kwargs):
        return f"[{self.extra['pair']}] {msg}", kwargs


def get_pair_logger(name: str, pair: str) -> PairLoggerAdapter:
    return PairLoggerAdapter(logging.getLogger(name), {"pair": pair})


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.APP_LOG_FILE),
        ],
    )
