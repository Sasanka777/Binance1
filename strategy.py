"""
Pure-function indicator math + entry/exit rule evaluation.

No network, no orders, no state. Given a closed-candle DataFrame this module
returns (signal_bool, reason_str). That keeps the strategy unit-testable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


# ---------------------------------------------------------------------------
# Indicator primitives
# ---------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return true_range(high, low, close).ewm(alpha=1 / period, adjust=False).mean()


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = true_range(high, low, close)
    atr_v = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = (
        100
        * pd.Series(plus_dm, index=high.index).ewm(alpha=1 / period, adjust=False).mean()
        / atr_v
    )
    minus_di = (
        100
        * pd.Series(minus_dm, index=high.index).ewm(alpha=1 / period, adjust=False).mean()
        / atr_v
    )
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx_val, plus_di, minus_di


# ---------------------------------------------------------------------------
# Build the full indicator frame in one shot
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema21"] = ema(df["close"], config.EMA_FAST)
    df["ema55"] = ema(df["close"], config.EMA_MEDIUM)
    df["ema200"] = ema(df["close"], config.EMA_SLOW)
    df["rsi"] = rsi(df["close"], config.RSI_PERIOD)
    df["atr"] = atr(df["high"], df["low"], df["close"], config.ATR_PERIOD)
    adx_v, plus_di, minus_di = adx(df["high"], df["low"], df["close"], config.ADX_PERIOD)
    df["adx"] = adx_v
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    df["volume_sma"] = df["volume"].rolling(config.VOLUME_SMA_PERIOD).mean()
    bb_mid = df["close"].rolling(config.BB_PERIOD).mean()
    bb_std = df["close"].rolling(config.BB_PERIOD).std()
    df["bb_upper"] = bb_mid + config.BB_STD * bb_std
    df["bb_lower"] = bb_mid - config.BB_STD * bb_std
    df["bb_width_pct"] = (df["bb_upper"] - df["bb_lower"]) / bb_mid
    return df


# ---------------------------------------------------------------------------
# Entry rule evaluation on the latest CLOSED candle (df.iloc[-1])
# ---------------------------------------------------------------------------
def check_long_entry(df: pd.DataFrame, last_24h_high: float) -> tuple[bool, str]:
    if len(df) < max(config.EMA_SLOW, config.PULLBACK_LOOKBACK + 2):
        return False, "insufficient history"

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Any indicator still NaN → reject
    required = ["ema21", "ema55", "ema200", "rsi", "atr", "adx",
                "plus_di", "minus_di", "volume_sma", "bb_width_pct"]
    if any(pd.isna(last[c]) for c in required) or pd.isna(prev["rsi"]):
        return False, "indicators not warm"

    # 1. Trend alignment
    if not (last["ema21"] > last["ema55"] > last["ema200"]):
        return False, "EMAs not stacked bullish"
    if last["close"] <= last["ema200"]:
        return False, "close <= EMA200"

    # 2. EMA separation — filter for actual trend vs crossover noise
    ema_spread = (last["ema21"] - last["ema55"]) / last["close"]
    if ema_spread < config.MIN_EMA_SPREAD_PCT:
        return False, f"EMA21/55 spread too tight: {ema_spread:.4%}"

    # 3. ADX trend strength + directional agreement
    if last["adx"] < config.ADX_THRESHOLD:
        return False, f"ADX {last['adx']:.1f} < {config.ADX_THRESHOLD}"
    if last["plus_di"] <= last["minus_di"]:
        return False, "+DI not above -DI"

    # 4. Pullback: within last N candles the low must have touched EMA21
    touched = False
    for i in range(-config.PULLBACK_LOOKBACK, 0):
        row = df.iloc[i]
        if row["low"] <= row["ema21"] * (1 + config.EMA_PULLBACK_TOLERANCE):
            touched = True
            break
    if not touched:
        return False, "no EMA21 pullback in last 3 candles"

    # 5. Current candle closes back above EMA21
    if last["close"] <= last["ema21"]:
        return False, "close <= EMA21"

    # 6. RSI momentum check — supports two modes
    if config.RSI_ENTRY_MODE == "CROSS":
        # Strict: RSI must cross above 50 on THIS candle (rare event)
        if not (prev["rsi"] < config.RSI_CROSS_LEVEL <= last["rsi"]):
            return False, f"RSI did not cross 50 up (prev={prev['rsi']:.1f}, curr={last['rsi']:.1f})"
    else:
        # ABOVE: RSI must be >= 50 now AND prev wasn't already deep in OB territory
        # — this fires whenever momentum is rising, not just the cross instant
        if last["rsi"] < config.RSI_CROSS_LEVEL:
            return False, f"RSI {last['rsi']:.1f} below {config.RSI_CROSS_LEVEL}"
        if prev["rsi"] >= config.RSI_PREV_MAX:
            return False, f"RSI already extended (prev={prev['rsi']:.1f} >= {config.RSI_PREV_MAX})"
    if last["rsi"] >= config.RSI_MAX_LONG:
        return False, f"RSI too high: {last['rsi']:.1f}"

    # 7. Volume confirmation
    if last["volume"] < config.VOLUME_MULTIPLIER * last["volume_sma"]:
        return False, f"volume {last['volume']:.1f} < {config.VOLUME_MULTIPLIER}× SMA {last['volume_sma']:.1f}"

    # 8. Green candle with meaningful body
    if last["close"] <= last["open"]:
        return False, "candle not green"
    body = last["close"] - last["open"]
    if body < config.MIN_BODY_ATR_RATIO * last["atr"]:
        return False, f"body {body:.2f} < {config.MIN_BODY_ATR_RATIO}× ATR {last['atr']:.2f}"

    # 9. Don't chase the top
    if last["close"] > last_24h_high - config.MAX_HIGH_PROXIMITY_ATR * last["atr"]:
        return False, "too close to 24h high"

    # 10. Volatility sanity
    atr_pct = last["atr"] / last["close"]
    if atr_pct < config.MIN_ATR_PCT:
        return False, f"ATR% {atr_pct:.4%} too low"
    if atr_pct > config.MAX_ATR_PCT:
        return False, f"ATR% {atr_pct:.4%} too high"

    # 11. Bollinger width — reject squeezes
    if last["bb_width_pct"] < config.MIN_BB_WIDTH_PCT:
        return False, f"BB width {last['bb_width_pct']:.4%} too tight"

    return True, "LONG signal"


# ---------------------------------------------------------------------------
# Chandelier trailing stop
# ---------------------------------------------------------------------------
def chandelier_stop_long(highest_high_since_entry: float, atr_at_entry: float) -> float:
    return highest_high_since_entry - config.TRAILING_CHANDELIER_ATR * atr_at_entry
