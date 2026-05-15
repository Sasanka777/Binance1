"""Futures auto-bot configuration."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

_here = Path(__file__).parent
for _candidate in (_here / ".env", _here.parent / ".env", _here.parent / ".env.example"):
    if _candidate.exists():
        load_dotenv(_candidate, override=False)

# ---------------------------------------------------------------------------
# Binance Futures Testnet credentials (same keys as the telegram bot)
# ---------------------------------------------------------------------------
BINANCE_FUTURES_API_KEY = os.getenv("BINANCE_FUTURES_API_KEY", "")
BINANCE_FUTURES_API_SECRET = os.getenv("BINANCE_FUTURES_API_SECRET", "")
FUTURES_TESTNET = True

# ---------------------------------------------------------------------------
# Pair universe — top USDT-M perpetuals, STRICTER than the breakout build
# ---------------------------------------------------------------------------
PAIR_UNIVERSE_SIZE = 20                # was 30 — fewer, higher-quality pairs
QUOTE = "USDT"
MIN_DAILY_VOLUME_USDT = 200_000_000    # only pairs with $200M+ 24h volume
MIN_SYMBOL_AGE_DAYS = 60               # skip newer-than-2-months listings
EXCLUDE_BASES = ["USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI"]
# Bad performers from the breakout-run dataset — exclude until proven otherwise
BLACKLIST_BASES = [
    "HU", "BU", "AIOT", "NAORIS", "SAPIEN", "PLAY", "CHILLGUY",
    "USELESS", "AKE", "COLLECT", "SOLV", "SKYAI", "LAB", "RIF",
    "RDNT", "UB", "PO", "DYM", "FXS", "FIS", "GTC", "SXP", "SAGA",
]

# ---------------------------------------------------------------------------
# Timeframe
# ---------------------------------------------------------------------------
INTERVAL = "15m"
HISTORY_CANDLES = 120

# ---------------------------------------------------------------------------
# Strategy: MEAN REVERSION (oversold bounce / overbought rejection)
# ---------------------------------------------------------------------------
# RSI zones — relaxed to fire more often (was 25/75, way too strict)
RSI_PERIOD = 14
RSI_OVERSOLD = 35                      # LONG when RSI <= this (was 25)
RSI_OVERBOUGHT = 65                    # SHORT when RSI >= this (was 75)

# Bollinger Bands — entry near the band is enough (don't need full break)
BB_PERIOD = 20
BB_STD = 2.0
BB_PROXIMITY_PCT = 0.30                # close within 30 % of band edge counts

# Volume + volatility filters
VOLUME_MULTIPLIER = 1.2                # was 1.5 — accept moderate volume
VOLUME_SMA_PERIOD = 20
ATR_PERIOD = 14
MIN_ATR_PCT = 0.003                    # 0.3 % — was 0.4 %

# Reversal candle requirement — relaxed
MIN_BODY_ATR_RATIO = 0.2               # body >= 0.2 × ATR (was 0.3)

# ---------------------------------------------------------------------------
# Exit (TP1_QUICK style — full position close at TP or SL)
# ---------------------------------------------------------------------------
LEVERAGE = 20
TP_MARGIN_PCT = 30                     # +30 % margin profit (1.5 % price move)
SL_MARGIN_PCT = 25                     # -25 % margin loss (1.25 % price move)
                                       # MR trades need more room. R:R ≈ 1.2:1.

# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------
FIXED_MARGIN_USDT = 3.0
MAX_CONCURRENT = 5
MAX_DAILY_LOSS_USDT = 15.0             # tighter — halt earlier on bad days
COOLDOWN_AFTER_TRADE_MIN = 60          # 1 h cooldown per pair (was 30 min)

# ---------------------------------------------------------------------------
# Logging / runtime
# ---------------------------------------------------------------------------
SIGNAL_LOG = "fb_signals.csv"
TRADE_LOG = "fb_trades.csv"
APP_LOG = "fb_bot.log"
STATE_FILE = "fb_state.json"
LOG_LEVEL = "INFO"
POSITION_POLL_SECONDS = 60
