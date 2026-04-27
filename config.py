"""All tunable constants live here. No logic, no imports from other modules."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Prefer .env if present, otherwise fall back to .env.example.
_here = Path(__file__).parent
for _candidate in (_here / ".env", _here / ".env.example"):
    if _candidate.exists():
        load_dotenv(_candidate, override=False)

# ---------------------------------------------------------------------------
# API credentials (Binance Spot Testnet — https://testnet.binance.vision/)
# ---------------------------------------------------------------------------
API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "")
API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET", "")
TESTNET = True

# ---------------------------------------------------------------------------
# Market — multi-pair
# ---------------------------------------------------------------------------
PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
INTERVAL = "15m"
KLINE_LIMIT = 300  # initial history load (need >=200 for EMA200 warmup)

# ---------------------------------------------------------------------------
# Indicator periods
# ---------------------------------------------------------------------------
EMA_FAST = 21
EMA_MEDIUM = 55
EMA_SLOW = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
VOLUME_SMA_PERIOD = 20
BB_PERIOD = 20
BB_STD = 2.0

# ---------------------------------------------------------------------------
# Entry thresholds — AGGRESSIVE preset (target 1-3 trades/day across 3 pairs)
# Original STRICT values shown in comments (use those for higher win rate,
# fewer trades — original conservative defaults).
# ---------------------------------------------------------------------------
ADX_THRESHOLD = 18.0           # STRICT: 25.0  (lower = trade weaker trends)
RSI_CROSS_LEVEL = 50.0
RSI_PREV_MAX = 60.0            # in ABOVE mode, prev RSI must be < this
RSI_ENTRY_MODE = "ABOVE"       # "CROSS" (strict, rare) | "ABOVE" (frequent)
RSI_MAX_LONG = 75.0            # STRICT: 70.0
RSI_MIN_SHORT = 25.0
VOLUME_MULTIPLIER = 1.0        # STRICT: 1.2  (1.0 = at avg volume, no spike needed)
MIN_BODY_ATR_RATIO = 0.25      # STRICT: 0.4  (accept smaller candle bodies)
MIN_ATR_PCT = 0.0010           # STRICT: 0.0030  (accept lower volatility)
MAX_ATR_PCT = 0.0500           # 5.00 %
MIN_BB_WIDTH_PCT = 0.008       # STRICT: 0.015  (accept tighter ranges)
MIN_EMA_SPREAD_PCT = 0.0005    # STRICT: 0.002  (accept closer EMAs)
EMA_PULLBACK_TOLERANCE = 0.005  # STRICT: 0.002  (looser pullback definition)
MAX_HIGH_PROXIMITY_ATR = 0.2   # STRICT: 0.5  (allow chasing closer to 24h high)
PULLBACK_LOOKBACK = 5          # STRICT: 3    (wider pullback window)

# ---------------------------------------------------------------------------
# Exit parameters (all expressed as ATR multiples)
# ---------------------------------------------------------------------------
STOP_LOSS_ATR = 1.0
TAKE_PROFIT_1_ATR = 1.5
TRAILING_CHANDELIER_ATR = 2.0
PARTIAL_CLOSE_RATIO = 0.50
TIME_STOP_CANDLES = 48         # 48 * 15m = 12 h

# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------
RISK_PER_TRADE = 0.01          # 1.0 %
MAX_DAILY_LOSS = 0.03          # 3.0 %  (halt trading until UTC midnight)
MAX_CONSECUTIVE_LOSSES = 3
MAX_DRAWDOWN = 0.10            # 10 %  hard kill switch

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
LOG_FILE = "trades.csv"
STATE_FILE = "state.json"
APP_LOG_FILE = "bot.log"
LOG_LEVEL = "INFO"
ORDER_POLL_SECONDS = 2         # how long to wait after placing an order before querying status
