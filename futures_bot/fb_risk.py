"""
Risk gates for the futures auto-bot.

Enforces:
  - max concurrent positions
  - daily loss limit (UTC midnight reset)
  - per-pair cooldown after a closed trade
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from . import fb_config as config


class RiskManager:
    def __init__(self) -> None:
        self.daily_pnl_usdt: float = 0.0
        self.daily_reset_day: str | None = None
        self._open_pairs: set[str] = set()
        self._cooldown_until: dict[str, float] = {}     # pair → wall-time epoch
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _reset_daily_if_needed(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.daily_reset_day != today:
            self.daily_reset_day = today
            self.daily_pnl_usdt = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def can_open(self, pair: str) -> tuple[bool, str]:
        with self._lock:
            self._reset_daily_if_needed()

            if self.daily_pnl_usdt <= -config.MAX_DAILY_LOSS_USDT:
                return False, f"daily loss limit ({self.daily_pnl_usdt:.2f})"

            if pair in self._open_pairs:
                return False, "already open on this pair"

            if len(self._open_pairs) >= config.MAX_CONCURRENT:
                return False, f"max concurrent positions ({config.MAX_CONCURRENT})"

            cd = self._cooldown_until.get(pair, 0.0)
            now = time.time()
            if now < cd:
                return False, f"cooldown {int(cd - now)}s"

            return True, "ok"

    def mark_open(self, pair: str) -> None:
        with self._lock:
            self._open_pairs.add(pair)

    def mark_closed(self, pair: str, pnl_usdt: float = 0.0) -> None:
        with self._lock:
            self._open_pairs.discard(pair)
            self.daily_pnl_usdt += pnl_usdt
            self._cooldown_until[pair] = time.time() + config.COOLDOWN_AFTER_TRADE_MIN * 60

    def open_pairs(self) -> list[str]:
        with self._lock:
            return list(self._open_pairs)

    @property
    def open_count(self) -> int:
        with self._lock:
            return len(self._open_pairs)
