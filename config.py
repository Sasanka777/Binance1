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
# Entry thresholds
# ---------------------------------------------------------------------------
ADX_THRESHOLD = 25.0
RSI_CROSS_LEVEL = 50.0
RSI_MAX_LONG = 70.0
RSI_MIN_SHORT = 30.0
VOLUME_MULTIPLIER = 1.2
MIN_BODY_ATR_RATIO = 0.4
MIN_ATR_PCT = 0.0030          # 0.30 %
MAX_ATR_PCT = 0.0500          # 5.00 %
MIN_BB_WIDTH_PCT = 0.015      # 1.50 %
MIN_EMA_SPREAD_PCT = 0.002    # 0.20 %  (EMA21 vs EMA55 separation / price)
EMA_PULLBACK_TOLERANCE = 0.002  # 0.20 %  (how close to EMA21 the pullback must get)
MAX_HIGH_PROXIMITY_ATR = 0.5   # skip longs within 0.5 ATR of 24h high
PULLBACK_LOOKBACK = 3          # candles to look back for pullback touch

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
