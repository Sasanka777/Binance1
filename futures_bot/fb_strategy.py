"""
Mean-reversion strategy.

The premise:
  - Crypto alts that pump 5%+ tend to dump back (overbought → rejection).
  - Crypto alts that crash 5%+ tend to bounce (oversold → recovery).
  - Trade the REVERSAL, not the continuation.

This avoids the classic breakout trap (buy the high → get dumped on).

LONG entry  (5 confirmations — oversold bounce):
  1. RSI(14) <= 25
  2. Close <= lower Bollinger Band (price is statistically extreme)
  3. Current candle is GREEN (close > open) — first sign of reversal
  4. Candle body >= 0.3 × ATR (real reversal, not a doji)
  5. Volume >= 1.5 × SMA20 (capitulation/reversal volume)
  6. ATR% >= 0.4 % (enough movement to profit after fees)

SHORT entry (mirror — overbought rejection):
  1. RSI(14) >= 75
  2. Close >= upper Bollinger Band
  3. Current candle is RED (close < open)
  4. Body >= 0.3 × ATR
  5. Volume >= 1.5 × SMA20
  6. ATR% >= 0.4 %
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import fb_config as config


# ---------------------------------------------------------------------------
# Indicator primitives
# ---------------------------------------------------------------------------
def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def true_range(high, low, close):
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(high, low, close, period=14):
    return true_range(high, low, close).ewm(alpha=1 / period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Indicator frame
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = rsi(df["close"], config.RSI_PERIOD)

    bb_mid = df["close"].rolling(config.BB_PERIOD).mean()
    bb_std = df["close"].rolling(config.BB_PERIOD).std()
    df["bb_mid"] = bb_mid
    df["bb_upper"] = bb_mid + config.BB_STD * bb_std
    df["bb_lower"] = bb_mid - config.BB_STD * bb_std

    df["volume_sma"] = df["volume"].rolling(config.VOLUME_SMA_PERIOD).mean()
    df["atr"] = atr(df["high"], df["low"], df["close"], config.ATR_PERIOD)
    df["atr_pct"] = df["atr"] / df["close"]
    return df


# ---------------------------------------------------------------------------
# Entry rules
# ---------------------------------------------------------------------------
def check_long(df: pd.DataFrame) -> tuple[bool, str]:
    """Oversold-bounce LONG entry."""
    needed = max(config.BB_PERIOD, config.RSI_PERIOD, config.ATR_PERIOD) + 5
    if len(df) < needed:
        return False, "insufficient history"

    last = df.iloc[-1]
    required = ["rsi", "bb_lower", "volume_sma", "atr", "atr_pct"]
    if any(pd.isna(last[c]) for c in required):
        return False, "indicators not warm"

    # 1. RSI deeply oversold
    if last["rsi"] > config.RSI_OVERSOLD:
        return False, f"RSI {last['rsi']:.1f} > {config.RSI_OVERSOLD} (not oversold)"

    # 2. Price below lower Bollinger Band (statistically extreme)
    if last["close"] > last["bb_lower"]:
        return False, f"close {last['close']:.6g} > BB_lower {last['bb_lower']:.6g}"

    # 3. Reversal signal — green candle
    if last["close"] <= last["open"]:
        return False, "candle not green (no reversal signal)"

    # 4. Body size — must be a meaningful candle, not a doji
    body = last["close"] - last["open"]
    if body < config.MIN_BODY_ATR_RATIO * last["atr"]:
        return False, f"body too small ({body:.6g} < {config.MIN_BODY_ATR_RATIO}×ATR)"

    # 5. Volume confirmation
    if last["volume"] < config.VOLUME_MULTIPLIER * last["volume_sma"]:
        return False, f"volume weak ({last['volume']:.0f} < {config.VOLUME_MULTIPLIER}×SMA)"

    # 6. Volatility
    if last["atr_pct"] < config.MIN_ATR_PCT:
        return False, f"ATR% {last['atr_pct']:.4%} < {config.MIN_ATR_PCT:.2%}"

    return True, "LONG"


def check_short(df: pd.DataFrame) -> tuple[bool, str]:
    """Overbought-rejection SHORT entry."""
    needed = max(config.BB_PERIOD, config.RSI_PERIOD, config.ATR_PERIOD) + 5
    if len(df) < needed:
        return False, "insufficient history"

    last = df.iloc[-1]
    required = ["rsi", "bb_upper", "volume_sma", "atr", "atr_pct"]
    if any(pd.isna(last[c]) for c in required):
        return False, "indicators not warm"

    if last["rsi"] < config.RSI_OVERBOUGHT:
        return False, f"RSI {last['rsi']:.1f} < {config.RSI_OVERBOUGHT} (not overbought)"

    if last["close"] < last["bb_upper"]:
        return False, f"close {last['close']:.6g} < BB_upper {last['bb_upper']:.6g}"

    if last["close"] >= last["open"]:
        return False, "candle not red (no rejection signal)"

    body = last["open"] - last["close"]
    if body < config.MIN_BODY_ATR_RATIO * last["atr"]:
        return False, f"body too small"

    if last["volume"] < config.VOLUME_MULTIPLIER * last["volume_sma"]:
        return False, f"volume weak"

    if last["atr_pct"] < config.MIN_ATR_PCT:
        return False, f"ATR% too low"

    return True, "SHORT"
