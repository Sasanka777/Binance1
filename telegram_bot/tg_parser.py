"""
Regex-based parser for crypto-signal Telegram messages.

Supports two channel formats currently:

  ── Format A (Crypto Path VIP) ──
      Coin : CHIP / USDT
      Position: SHORT
      Entry Point: Market
      Profit Targets:
      (1) +15%
      ...
      Stop Loss: 0.1050              ← absolute price
      Leverage Range: 15x - 20x

  ── Format B (Crypto Advance VIP) ──
      Pair:LABUSDT
      Position:LONG
      Leverage: 20X(CROSS)
      Entry Zone: (MARKET)
      Stop Loss: 75% (Strict)        ← percentage of margin loss
      TP1 – 30%
      TP2 – 60%
      ...

The Signal dataclass is format-agnostic; downstream code uses `sl_is_pct` to
know whether `stop_loss` is an absolute price or a margin-loss percentage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Quote currencies we know how to split out from a concatenated pair like
# "LABUSDT". Order matters: longer suffixes first so "USDCUSDT" splits correctly.
KNOWN_QUOTES = ("FDUSD", "USDT", "USDC", "BUSD", "TUSD")


@dataclass
class Signal:
    coin: str
    quote: str
    side: str                              # "LONG" or "SHORT"
    entry_type: str = "MARKET"             # "MARKET" or "LIMIT"
    entry_price: Optional[float] = None
    stop_loss: float = 0.0                 # interpretation depends on sl_is_pct
    sl_is_pct: bool = False                # True → stop_loss is % of margin loss
    take_profits: list[float] = field(default_factory=list)
    leverage_min: int = 1
    leverage_max: int = 1
    raw_text: str = ""

    @property
    def symbol(self) -> str:
        return f"{self.coin}{self.quote}"


# --- Pair patterns --------------------------------------------------------
# Format A: "Coin: ABC / USDT"
COIN_SLASH_RE = re.compile(
    r"Coin\s*:\s*([A-Z0-9]{2,12})\s*/\s*([A-Z]{3,6})", re.IGNORECASE
)
# Format B: "Pair: ABCUSDT" (concatenated — split via known quote suffixes)
PAIR_CONCAT_RE = re.compile(
    r"Pair\s*:\s*([A-Z0-9]{4,16})\b", re.IGNORECASE
)

SIDE_RE = re.compile(r"Position\s*:?\s*(LONG|SHORT|BUY|SELL)", re.IGNORECASE)
ENTRY_RE = re.compile(r"Entry\s*(?:Point|Zone)?\s*:?\s*([^\n]+)", re.IGNORECASE)

# TP pattern accepts:  (1) +15%   |   TP1 – 30%   |   TP1: 30%
# REQUIRES either "TPx" or "(x)" prefix so it doesn't accidentally swallow
# percentages from other lines (e.g. "Stop Loss: 75%").
TP_RE = re.compile(
    r"(?:TP\s*(\d{1,2})|\(\s*(\d{1,2})\s*\))\s*[-–:+]?\s*([\d.]+)\s*%",
    re.IGNORECASE,
)

# SL — try percentage first (must have % sign), then fall back to plain number
SL_PCT_RE = re.compile(r"Stop\s*Loss\s*:?\s*([\d.]+)\s*%", re.IGNORECASE)
SL_PRICE_RE = re.compile(r"Stop\s*Loss\s*:?\s*([\d.]+)(?!\s*%)", re.IGNORECASE)

# Leverage — range first (15x - 20x), then single value (20X)
LEV_RANGE_RE = re.compile(
    r"Leverage[^:]*:\s*(\d{1,3})\s*[xX]?\s*[-–]\s*(\d{1,3})\s*[xX]?",
    re.IGNORECASE,
)
LEV_SINGLE_RE = re.compile(
    r"Leverage[^:]*:\s*(\d{1,3})\s*[xX]", re.IGNORECASE
)


def _split_concat_pair(pair: str) -> tuple[str, str] | None:
    """Split a concatenated symbol like 'LABUSDT' into ('LAB', 'USDT')."""
    p = pair.upper()
    for q in KNOWN_QUOTES:
        if p.endswith(q) and len(p) > len(q):
            return p[: -len(q)], q
    return None


def parse_signal(text: Optional[str]) -> Optional[Signal]:
    if not text:
        return None

    # --- Pair / Coin ------------------------------------------------------
    coin: str | None = None
    quote: str | None = None
    m = COIN_SLASH_RE.search(text)
    if m:
        coin, quote = m.group(1).upper(), m.group(2).upper()
    else:
        m = PAIR_CONCAT_RE.search(text)
        if m:
            split = _split_concat_pair(m.group(1))
            if split:
                coin, quote = split

    if not (coin and quote):
        return None

    # --- Side -------------------------------------------------------------
    side_m = SIDE_RE.search(text)
    if not side_m:
        return None
    side_raw = side_m.group(1).upper()
    side = "LONG" if side_raw in ("LONG", "BUY") else "SHORT"

    # --- Stop Loss --------------------------------------------------------
    sl_value: float | None = None
    sl_is_pct = False
    sl_pct_m = SL_PCT_RE.search(text)
    if sl_pct_m:
        try:
            sl_value = float(sl_pct_m.group(1))
            sl_is_pct = True
        except ValueError:
            pass
    if sl_value is None:
        sl_price_m = SL_PRICE_RE.search(text)
        if sl_price_m:
            try:
                sl_value = float(sl_price_m.group(1))
            except ValueError:
                pass
    if sl_value is None or sl_value <= 0:
        return None

    # --- Take Profits -----------------------------------------------------
    tps: list[float] = []
    for m in TP_RE.finditer(text):
        # Group 1 = "TPx" form; Group 2 = "(x)" form; Group 3 = percentage
        num_str = m.group(1) or m.group(2)
        pct_str = m.group(3)
        try:
            num = int(num_str)
            pct = float(pct_str)
        except (ValueError, TypeError):
            continue
        if 1 <= num <= 10 and 0 < pct <= 1000:
            tps.append(pct)
    # Dedup preserving order, max 5
    seen: set[float] = set()
    deduped: list[float] = []
    for p in tps:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    tps = deduped[:5]

    if not tps:
        # SL alone without TPs is not a valid signal in our system
        return None

    # --- Leverage ---------------------------------------------------------
    lev_min = lev_max = 1
    lev_m = LEV_RANGE_RE.search(text)
    if lev_m:
        try:
            lev_min = max(1, int(lev_m.group(1)))
            lev_max = max(lev_min, int(lev_m.group(2)))
        except ValueError:
            pass
    else:
        lev_m = LEV_SINGLE_RE.search(text)
        if lev_m:
            try:
                lev_min = lev_max = max(1, int(lev_m.group(1)))
            except ValueError:
                pass

    # --- Entry type / price -----------------------------------------------
    entry_type = "MARKET"
    entry_price: Optional[float] = None
    entry_m = ENTRY_RE.search(text)
    if entry_m:
        entry_str = entry_m.group(1).strip().upper()
        if "MARKET" not in entry_str:
            num_m = re.search(r"([\d.]+)", entry_str)
            if num_m:
                try:
                    entry_price = float(num_m.group(1))
                    entry_type = "LIMIT"
                except ValueError:
                    pass

    return Signal(
        coin=coin,
        quote=quote,
        side=side,
        entry_type=entry_type,
        entry_price=entry_price,
        stop_loss=sl_value,
        sl_is_pct=sl_is_pct,
        take_profits=tps,
        leverage_min=lev_min,
        leverage_max=lev_max,
        raw_text=text,
    )
