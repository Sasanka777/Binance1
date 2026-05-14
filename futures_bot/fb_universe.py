"""
Pair universe discovery — stricter version.

Picks top USDT-M PERPETUAL futures that satisfy:
  - status == TRADING
  - quote == USDT
  - base not in EXCLUDE_BASES or BLACKLIST_BASES
  - 24h quote volume >= MIN_DAILY_VOLUME_USDT
  - listed >= MIN_SYMBOL_AGE_DAYS ago (skip newly-listed manipulated pairs)

Sorted by 24h volume desc, top N returned.
"""
from __future__ import annotations

import logging
import time

from binance.client import Client

from . import fb_config as config

log = logging.getLogger(__name__)


def get_top_pairs(client: Client) -> list[str]:
    info = client.futures_exchange_info()
    now_ms = int(time.time() * 1000)
    min_age_ms = config.MIN_SYMBOL_AGE_DAYS * 24 * 3600 * 1000

    eligible: dict[str, dict] = {}
    for s in info["symbols"]:
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != config.QUOTE:
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        base = s.get("baseAsset", "")
        if base in config.EXCLUDE_BASES or base in config.BLACKLIST_BASES:
            continue
        onboard = s.get("onboardDate") or 0
        if onboard and (now_ms - onboard) < min_age_ms:
            continue
        eligible[s["symbol"]] = s

    if not eligible:
        log.error("No tradeable USDT-M perpetuals after filters — check config")
        return []

    tickers = client.futures_ticker()
    ranked: list[tuple[str, float]] = []
    for t in tickers:
        sym = t["symbol"]
        if sym not in eligible:
            continue
        try:
            vol = float(t["quoteVolume"])
        except (KeyError, ValueError):
            continue
        if vol < config.MIN_DAILY_VOLUME_USDT:
            continue
        ranked.append((sym, vol))

    ranked.sort(key=lambda x: x[1], reverse=True)
    top = [sym for sym, _ in ranked[: config.PAIR_UNIVERSE_SIZE]]

    log.info(
        "Pair universe (%d after filters): %s",
        len(top), ", ".join(top),
    )
    return top
