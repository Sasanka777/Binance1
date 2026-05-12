"""
Futures auto-bot configuration.

Shares Binance Futures Testnet API keys with the telegram bot (same .env).
Everything tunable here so the strategy can be tuned without code changes.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

_here = Path(__file__).parent
for _candidate in (_here / ".env", _here.parent / ".env", _here.parent / ".env.example"):
    if _candidate.exists():
        load_dotenv(_candidate, override=False)

# ---------------------------------------------------------------------------
# Binance Futures Testnet credentials (reuse telegram bot's keys)
# ---------------------------------------------------------------------------
BINANCE_FUTURES_API_KEY = os.getenv("BINANCE_FUTURES_API_KEY", "")
BINANCE_FUTURES_API_SECRET = os.getenv("BINANCE_FUTURES_API_SECRET", "")
FUTURES_TESTNET = True

# ---------------------------------------------------------------------------
# Pair universe — discovered automatically at startup
# ---------------------------------------------------------------------------
PAIR_UNIVERSE_SIZE = 30                # top N by 24h volume
QUOTE = "USDT"
EXCLUDE_BASES = ["USDC", "BUSD", "TUSD", "FDUSD", "USDP"]   # skip stable-stable pairs

# ---------------------------------------------------------------------------
# Timeframe + history
# ---------------------------------------------------------------------------
INTERVAL = "15m"
HISTORY_CANDLES = 100                  # per pair, kept in memory

# ---------------------------------------------------------------------------
# Entry filters — relaxed for ~5-15 trades/day across 30 pairs
# ---------------------------------------------------------------------------
BREAKOUT_LOOKBACK = 20                 # candle window for rolling high/low
VOLUME_MULTIPLIER = 1.5                # volume > 1.5 × SMA
RSI_PERIOD = 14
RSI_LONG_MIN = 50.0                    # long: RSI in [50, 75] — rising momentum
RSI_LONG_MAX = 75.0
RSI_SHORT_MIN = 25.0                   # short: RSI in [25, 50] — falling momentum
RSI_SHORT_MAX = 50.0
ATR_PERIOD = 14
MIN_ATR_PCT = 0.003                    # 0.3 % — skip dead markets

# ---------------------------------------------------------------------------
# Position sizing + exits — TP1_QUICK style
# ---------------------------------------------------------------------------
FIXED_MARGIN_USDT = 3.0                # capital per trade
LEVERAGE = 20                          # fixed 20x
TP_MARGIN_PCT = 30                     # +30 % margin profit  → +1.5 % price at 20x
SL_MARGIN_PCT = 10                     # -10 % margin loss   → -0.5 % price at 20x

# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------
MAX_CONCURRENT = 5                     # max simultaneous open positions
MAX_DAILY_LOSS_USDT = 30.0             # halt for the day if PnL drops below this
COOLDOWN_AFTER_TRADE_MIN = 30          # minutes before same pair can re-trade

# ---------------------------------------------------------------------------
# Logging / runtime
# ---------------------------------------------------------------------------
SIGNAL_LOG = "fb_signals.csv"          # every accepted/blocked signal
TRADE_LOG = "fb_trades.csv"            # opened trades
APP_LOG = "fb_bot.log"
LOG_LEVEL = "INFO"
POSITION_POLL_SECONDS = 60             # how often to check for closed positions
