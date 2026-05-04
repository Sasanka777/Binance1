"""Telegram listener + Binance Futures Testnet configuration.

Reads .env from the project root (parent dir) so we share creds with the spot bot.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from parent (d:/Binance/.env) or local (d:/Binance/telegram_bot/.env)
_here = Path(__file__).parent
for _candidate in (_here / ".env", _here.parent / ".env", _here.parent / ".env.example"):
    if _candidate.exists():
        load_dotenv(_candidate, override=False)

# ---------------------------------------------------------------------------
# Telegram credentials (https://my.telegram.org → API development tools)
# ---------------------------------------------------------------------------
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_PHONE = os.getenv("TG_PHONE", "")            # e.g. +94771234567
# TG_CHANNEL can be either:
#   - a public username (string): "cryptopathcommunity"
#   - a numeric channel ID (int): 2247568319  (use this for private channels)
_tg_channel_raw = os.getenv("TG_CHANNEL", "cryptopathcommunity").strip()
try:
    TG_CHANNEL: int | str = int(_tg_channel_raw)
except ValueError:
    TG_CHANNEL = _tg_channel_raw

# ---------------------------------------------------------------------------
# Binance Futures Testnet (https://testnet.binancefuture.com)
# Separate API keys from the spot testnet — register/login on that site,
# then "API Key" tab to generate.
# ---------------------------------------------------------------------------
BINANCE_FUTURES_API_KEY = os.getenv("BINANCE_FUTURES_API_KEY", "")
BINANCE_FUTURES_API_SECRET = os.getenv("BINANCE_FUTURES_API_SECRET", "")
FUTURES_TESTNET = True

# ---------------------------------------------------------------------------
# Trading safety knobs — start conservative, tune later
# ---------------------------------------------------------------------------
PAPER_MODE = False                 # True = log signals, do NOT place real orders
# Leverage strategy:
#   "MAX"  - use signal's max leverage (most aggressive, what signal allows)
#   "MIN"  - use signal's min leverage (most conservative)
#   "MID"  - use average of min/max
LEVERAGE_USE = "MAX"
LEVERAGE_CAP = 25                  # absolute sanity ceiling (testnet only)

# Position sizing — choose ONE mode:
# (A) FIXED_MARGIN_USDT > 0 → every trade uses exactly this much capital (margin).
#     Notional = FIXED_MARGIN_USDT × leverage. Worst-case loss per trade is this $.
# (B) FIXED_MARGIN_USDT = 0 → fall back to risk-based sizing (RISK_PER_TRADE_PCT
#     of account, capped at MAX_POSITION_USDT notional).
FIXED_MARGIN_USDT = 3.0            # $3 capital per trade (testnet test value)
RISK_PER_TRADE_PCT = 0.01          # 1 % — only used if FIXED_MARGIN_USDT == 0
MAX_POSITION_USDT = 1000.0         # cap notional — only used if FIXED_MARGIN_USDT == 0
ALLOWED_QUOTES = ["USDT"]
BLACKLIST_BASES: list[str] = []    # e.g. ["DOGE", "SHIB"] — symbols to skip
SIGNAL_MAX_AGE_SEC = 600           # ignore signals > 10 min old

# Exit strategy:
#   "HYBRID"        - close TP1 small lock + trailing stop on rest (LET PROFITS RUN)
#   "LADDER"        - close 30/25/20/15/10 % at TP1-5 (consistent profits)
#   "PURE_TRAILING" - no fixed TPs, single trailing stop from entry
EXIT_STRATEGY = "HYBRID"

# HYBRID-specific:
HYBRID_TP1_CLOSE_PCT = 0.10            # close 10 % at TP1 (less locked, more rides trail)
TRAILING_CALLBACK_RATE_PCT = 3.0       # 3 % buffer from peak — survives normal bounces,
                                       # catches big multi-percent moves that 1.5 % cuts short

# LADDER-specific (used only when EXIT_STRATEGY = "LADDER"):
TP_WEIGHTS = [0.30, 0.25, 0.20, 0.15, 0.10]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
SIGNAL_LOG = "signals.csv"          # all received signals (accepted + skipped)
TRADE_LOG = "tg_trades.csv"         # only executed trades
APP_LOG = "tg_bot.log"
SESSION_NAME = "tg_session"         # Telethon session file (.session extension added)
