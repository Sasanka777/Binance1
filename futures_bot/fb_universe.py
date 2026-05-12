"""
Discovers the top N USDT-margined perpetual futures pairs by 24h quote volume.

Run at startup; the result is the bot's pair universe for that session.
"""
from __future__ import annotations

import logging

from binance.client import Client

from . import fb_config as config

log = logging.getLogger(__name__)


def get_top_pairs(client: Client) -> list[str]:
    """Return the top `PAIR_UNIVERSE_SIZE` PERPETUAL USDT-M futures symbols
    available on the connected endpoint, ranked by 24h quote volume."""
    info = client.futures_exchange_info()

    # Available symbols: TRADING status, USDT quote, perpetual, not stable-stable
    available: dict[str, dict] = {}
    for s in info["symbols"]:
        if s.get("status") != "TRADING":
            continue
        if s.get("quoteAsset") != config.QUOTE:
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("baseAsset") in config.EXCLUDE_BASES:
            continue
        available[s["symbol"]] = s

    if not available:
        log.error("No tradeable USDT-M perpetuals found on this endpoint")
        return []

    # 24h volume rankings
    tickers = client.futures_ticker()
    ranked: list[tuple[str, float]] = []
    for t in tickers:
        sym = t["symbol"]
        if sym in available:
            try:
                vol = float(t["quoteVolume"])
            except (KeyError, ValueError):
                continue
            ranked.append((sym, vol))

    ranked.sort(key=lambda x: x[1], reverse=True)
    top = [sym for sym, _ in ranked[: config.PAIR_UNIVERSE_SIZE]]
    log.info("Pair universe (%d): %s", len(top), ", ".join(top))
    return top
