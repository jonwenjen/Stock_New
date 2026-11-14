"""Evidence grades for the leading-indicator cards (five-team review 2026-11-14, proposal quant-1).

Each card's value is tested against the TAIEX on this site's own data: the return over the next 5
and 20 sessions, and the worst close within the next 20 sessions (dd20). The value is lagged one
session, so nothing published after the Taipei close (US markets, the Fed) is used early.
Statistic: Spearman rank correlation, with a Newey-West t (lag = horizon) because overlapping
windows share returns. The grade uses the target with the largest |t|. Thresholds were fixed
before looking and are not tuned:

    A 有支持  750+ sessions, |t| >= 2 and the same sign in both halves of the sample
    B 證據弱  |t| >= 2, not A
    C 無證據  |t| < 2
    D 歷史不足 under 120 sessions

A card keeps its up/down colour only when it is A or B and the measured sign agrees with the way
the card reads (orient: +1 = a higher value is read as bullish, -1 = as bearish).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GRADE_TEXT = {"A": "有支持", "B": "證據弱", "C": "無證據", "D": "歷史不足"}
MIN_DAYS, LONG_DAYS = 120, 750


def nw_t(x: np.ndarray, y: np.ndarray, lag: int) -> float:
    """t statistic of the slope of y on x (both demeaned) with Newey-West (Bartlett) standard errors."""
    x, y = x - x.mean(), y - y.mean()
    sxx = float((x * x).sum())
    if sxx == 0:
        return 0.0
    b = float((x * y).sum()) / sxx
    u = x * (y - b * x)
    s = float((u * u).sum())
    for k in range(1, min(lag, len(u) - 1) + 1):
        s += 2 * (1 - k / (lag + 1)) * float((u[k:] * u[:-k]).sum())
    return b / np.sqrt(s / sxx ** 2) if s > 0 else 0.0


def targets(close: pd.Series) -> dict[str, tuple[pd.Series, int]]:
    """Forward 5- and 20-session returns and the 20-session drawdown, each with its overlap lag."""
    close = close.dropna()
    low20 = close[::-1].rolling(20, min_periods=20).min()[::-1].shift(-1)
    return {"f5": (close.shift(-5) / close - 1, 5), "f20": (close.shift(-20) / close - 1, 20),
            "dd20": (low20 / close - 1, 20)}


def evidence(x: pd.Series | None, close: pd.Series, orient: int) -> dict:
    """Grade one card. x: the card's daily value history; close: the TAIEX."""
    if x is None:
        return {"grade": "D", "text": GRADE_TEXT["D"], "n": 0}
    x = x.replace([np.inf, -np.inf], np.nan).dropna()
    close = close.dropna()
    if not len(x):
        return {"grade": "D", "text": GRADE_TEXT["D"], "n": 0}
    known = x.reindex(x.index.union(close.index)).ffill().reindex(close.index).shift(1)
    known = known[known.index >= x.index[0]]
    recent = x.tail(250)
    pct = round(float((recent <= x.iloc[-1]).mean()), 3) if len(recent) >= 60 else None
    n = int(known.notna().sum())
    if n < MIN_DAYS:
        return {"grade": "D", "text": GRADE_TEXT["D"], "n": n, "pct": pct}
    best = None
    for name, (y, lag) in targets(close).items():
        df = pd.concat([known, y], axis=1, keys=["x", "y"], sort=True).dropna()
        if len(df) < MIN_DAYS - 20:
            continue
        rx, ry = df["x"].rank().to_numpy(float), df["y"].rank().to_numpy(float)
        rho = float(np.corrcoef(rx, ry)[0, 1])
        t = nw_t(rx, ry, lag)
        half = len(df) // 2
        sp = lambda a, b: float(a.rank().corr(b.rank()))  # noqa: E731 - Spearman without scipy
        h1 = sp(df["x"].iloc[:half], df["y"].iloc[:half])
        h2 = sp(df["x"].iloc[half:], df["y"].iloc[half:])
        if best is None or abs(t) > abs(best["t"]):
            best = {"target": name, "rho": round(rho, 3), "t": round(float(t), 2), "halves": np.sign(h1) == np.sign(h2) != 0}
    if best is None or not np.isfinite(best["t"]):
        return {"grade": "D", "text": GRADE_TEXT["D"], "n": n, "pct": pct}
    strong = abs(best["t"]) >= 2
    grade = "A" if strong and n >= LONG_DAYS and best["halves"] else "B" if strong else "C"
    return {"grade": grade, "text": GRADE_TEXT[grade], "n": n, "pct": pct, "target": best["target"],
            "rho": best["rho"], "t": best["t"], "agree": bool(np.sign(best["rho"]) == np.sign(orient) != 0)}
