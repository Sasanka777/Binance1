"""
Regex-based parser for Crypto Path VIP-style signals.

Sample input::

    💠Crypto Path VIP 💠
    Coin : CHIP   / USDT
    Position: SHORT  🔴
    Entry Point: Market
    Profit Targets :
    (1) +15%
    (2) +30%
    (3) +50%
    (4) +80%
    (5) +100%
    Stop Loss: 0.1050
    Leverage Range: 15x - 20x

The parser is tolerant of extra whitespace, emojis, and minor format drift.
Returns a Signal dataclass or None if the message isn't a recognised signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Signal:
    coin: str
    quote: str
    side: str                              # "LONG" or "SHORT"
    entry_type: str = "MARKET"             # "MARKET" or "LIMIT"
    entry_price: Optional[float] = None    # only for LIMIT
    stop_loss: float = 0.0
    take_profits: list[float] = field(default_factory=list)  # percentages, 1..N
    leverage_min: int = 1
    leverage_max: int = 1
    raw_text: str = ""

    @property
    def symbol(self) -> str:
        return f"{self.coin}{self.quote}"


# --- Patterns -------------------------------------------------------------
COIN_RE = re.compile(r"Coin\s*:\s*([A-Z0-9]{2,12})\s*/\s*([A-Z]{3,6})", re.IGNORECASE)
SIDE_RE = re.compile(r"Position\s*:\s*(LONG|SHORT|BUY|SELL)", re.IGNORECASE)
ENTRY_RE = re.compile(r"Entry\s*Point?\s*:\s*([^\n]+)", re.IGNORECASE)
TP_RE = re.compile(r"\(?\s*(\d{1,2})\s*\)?\s*[+\-]?\s*([\d.]+)\s*%")
SL_RE = re.compile(r"Stop\s*Loss\s*:?\s*([\d.]+)", re.IGNORECASE)
LEV_RE = re.compile(
    r"Leverage[^:]*:\s*(\d{1,3})\s*x?\s*[-–]\s*(\d{1,3})\s*x?",
    re.IGNORECASE,
)


def parse_signal(text: Optional[str]) -> Optional[Signal]:
    if not text:
        return None

    coin_m = COIN_RE.search(text)
    side_m = SIDE_RE.search(text)
    sl_m = SL_RE.search(text)

    # Hard requirements: coin, side, SL must all be present
    if not (coin_m and side_m and sl_m):
        return None

    coin = coin_m.group(1).upper()
    quote = coin_m.group(2).upper()

    side_raw = side_m.group(1).upper()
    side = "LONG" if side_raw in ("LONG", "BUY") else "SHORT"

    try:
        sl = float(sl_m.group(1))
    except ValueError:
        return None
    if sl <= 0:
        return None

    # Take profits — accept up to 10, must be sane percentages
    tps: list[float] = []
    for m in TP_RE.finditer(text):
        try:
            num = int(m.group(1))
            pct = float(m.group(2))
        except (ValueError, IndexError):
            continue
        if 1 <= num <= 10 and 0 < pct <= 1000:
            tps.append(pct)
    # Dedup while preserving order, keep at most 5
    seen: set[float] = set()
    deduped: list[float] = []
    for p in tps:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    tps = deduped[:5]

    # Leverage range (default 1x if missing)
    lev_m = LEV_RE.search(text)
    if lev_m:
        try:
            lev_min = max(1, int(lev_m.group(1)))
            lev_max = max(lev_min, int(lev_m.group(2)))
        except ValueError:
            lev_min = lev_max = 1
    else:
        lev_min = lev_max = 1

    # Entry: MARKET vs LIMIT
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
        stop_loss=sl,
        take_profits=tps,
        leverage_min=lev_min,
        leverage_max=lev_max,
        raw_text=text,
    )
