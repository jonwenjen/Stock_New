"""Pure technical-indicator functions (pandas Series in, Series out)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI, 0..100."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0).where(up.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def daily_vol(close: pd.Series, n: int = 60) -> pd.Series:
    """Rolling std-dev of daily log returns."""
    return np.log(close / close.shift(1)).rolling(n, min_periods=max(20, n // 2)).std()


def pct_change_n(close: pd.Series, n: int) -> pd.Series:
    return close / close.shift(n) - 1


def range_position(close: pd.Series, n: int = 250) -> pd.Series:
    """Where close sits in the trailing n-day high/low range, 0..1."""
    hi = close.rolling(n, min_periods=60).max()
    lo = close.rolling(n, min_periods=60).min()
    return (close - lo) / (hi - lo).replace(0, np.nan)


def max_drawdown(close: pd.Series, n: int = 250) -> float:
    c = close.tail(n)
    if c.empty:
        return float("nan")
    return float((c / c.cummax() - 1).min())
