"""
Momentum-breakout strategy with 4 relaxed filters.

Long: 20-candle high broken + volume > 1.5× SMA + RSI in [50, 75] + ATR% > 0.3 %
Short: 20-candle low broken + volume > 1.5× SMA + RSI in [25, 50] + ATR% > 0.3 %

Pure-function module — no side effects, no network. Returns (bool, reason).
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


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    return pd.concat(
        [high - low, (high - prev).abs(), (low - prev).abs()],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return true_range(high, low, close).ewm(alpha=1 / period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Indicator frame
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Use SHIFTED rolling windows so the last candle's breakout is compared to
    # the prior N candles, not including itself (otherwise it would always be
    # the highest by definition).
    df["rolling_high"] = df["high"].shift(1).rolling(config.BREAKOUT_LOOKBACK).max()
    df["rolling_low"] = df["low"].shift(1).rolling(config.BREAKOUT_LOOKBACK).min()
    df["volume_sma"] = df["volume"].shift(1).rolling(config.BREAKOUT_LOOKBACK).mean()
    df["rsi"] = rsi(df["close"], config.RSI_PERIOD)
    df["atr"] = atr(df["high"], df["low"], df["close"], config.ATR_PERIOD)
    df["atr_pct"] = df["atr"] / df["close"]
    return df


# ---------------------------------------------------------------------------
# Entry rule evaluation on the most recent CLOSED candle (df.iloc[-1])
# ---------------------------------------------------------------------------
def check_long(df: pd.DataFrame) -> tuple[bool, str]:
    if len(df) < config.BREAKOUT_LOOKBACK + 5:
        return False, "insufficient history"

    last = df.iloc[-1]
    required = ["rolling_high", "volume_sma", "rsi", "atr_pct"]
    if any(pd.isna(last[c]) for c in required):
        return False, "indicators not warm"

    # 1. Breakout above prior 20-candle high
    if last["close"] <= last["rolling_high"]:
        return False, f"no breakout (close {last['close']:.6g} <= 20H {last['rolling_high']:.6g})"

    # 2. Volume confirmation
    if last["volume"] < config.VOLUME_MULTIPLIER * last["volume_sma"]:
        return False, f"volume {last['volume']:.0f} < {config.VOLUME_MULTIPLIER}×SMA {last['volume_sma']:.0f}"

    # 3. RSI in rising-momentum zone
    if not (config.RSI_LONG_MIN <= last["rsi"] <= config.RSI_LONG_MAX):
        return False, f"RSI {last['rsi']:.1f} outside [{config.RSI_LONG_MIN:.0f}, {config.RSI_LONG_MAX:.0f}]"

    # 4. Minimum volatility
    if last["atr_pct"] < config.MIN_ATR_PCT:
        return False, f"ATR% {last['atr_pct']:.4%} < {config.MIN_ATR_PCT:.2%}"

    return True, "LONG"


def check_short(df: pd.DataFrame) -> tuple[bool, str]:
    if len(df) < config.BREAKOUT_LOOKBACK + 5:
        return False, "insufficient history"

    last = df.iloc[-1]
    required = ["rolling_low", "volume_sma", "rsi", "atr_pct"]
    if any(pd.isna(last[c]) for c in required):
        return False, "indicators not warm"

    if last["close"] >= last["rolling_low"]:
        return False, f"no breakdown (close {last['close']:.6g} >= 20L {last['rolling_low']:.6g})"

    if last["volume"] < config.VOLUME_MULTIPLIER * last["volume_sma"]:
        return False, f"volume weak"

    if not (config.RSI_SHORT_MIN <= last["rsi"] <= config.RSI_SHORT_MAX):
        return False, f"RSI {last['rsi']:.1f} outside [{config.RSI_SHORT_MIN:.0f}, {config.RSI_SHORT_MAX:.0f}]"

    if last["atr_pct"] < config.MIN_ATR_PCT:
        return False, f"ATR% {last['atr_pct']:.4%} too low"

    return True, "SHORT"
