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
TG_CHANNEL = os.getenv("TG_CHANNEL", "cryptopathcommunity")

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
RISK_PER_TRADE_PCT = 0.01          # 1 % of account at risk per trade
MAX_POSITION_USDT = 1000.0         # cap notional per trade ($1k of $100k testnet)
ALLOWED_QUOTES = ["USDT"]
BLACKLIST_BASES: list[str] = []    # e.g. ["DOGE", "SHIB"] — symbols to skip
SIGNAL_MAX_AGE_SEC = 600           # ignore signals > 10 min old

# Multi-TP exit weights — fraction of position to close at each TP (sums to 1.0)
TP_WEIGHTS = [0.30, 0.25, 0.20, 0.15, 0.10]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
SIGNAL_LOG = "signals.csv"          # all received signals (accepted + skipped)
TRADE_LOG = "tg_trades.csv"         # only executed trades
APP_LOG = "tg_bot.log"
SESSION_NAME = "tg_session"         # Telethon session file (.session extension added)
