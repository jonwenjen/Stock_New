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


def selection_bias(pit: dict[str, list[str]], closes: dict[str, dict[str, float]], today_codes: list[str],
                   top: int = 90, max_gap: int = 7, min_days: int = 60) -> dict | None:
    """How much a back-test on today's universe flatters itself (review 2026-11-14, risk-3 / quant-2).

    Chains day-to-day equal-weight returns of (a) the stocks that were the `top` most traded on each
    day (point-in-time) and (b) today's universe, over the same days. Pairs of archived days more than
    `max_gap` calendar days apart are skipped for both. A daily move beyond the 10% price limit is a
    corporate action in raw prices, not a return, and is left out of both."""
    days = sorted(d for d in closes if d in pit)
    pit_g = now_g = 1.0
    used, first, last = 0, None, None
    for d0, d1 in zip(days, days[1:]):
        if (pd.Timestamp(d1) - pd.Timestamp(d0)).days > max_gap:
            continue
        c0, c1 = closes[d0], closes[d1]

        def rets(codes):
            out = [c1[c] / c0[c] - 1 for c in codes if c0.get(c) and c1.get(c)]
            return [r for r in out if abs(r) <= 0.105]

        a, b = rets(pit[d0][:top]), rets(today_codes)
        if len(a) < 0.8 * top or len(b) < 0.8 * len(today_codes):
            continue
        pit_g *= 1 + float(np.mean(a))
        now_g *= 1 + float(np.mean(b))
        used += 1
        first, last = first or d0, d1
    if used < min_days:
        return None
    return {"from": first, "to": last, "days": used, "pit": round(pit_g - 1, 4), "today": round(now_g - 1, 4)}


def pick_weights(stops: list[float | None]) -> np.ndarray:
    """Default weights inside one market: proportional to min(25%, 1% / stop distance), summing to 1."""
    raw = np.array([min(0.25, 0.01 / max(s or 0.05, 0.01)) for s in stops], dtype=float)
    return raw / raw.sum() if raw.sum() else raw


def portfolio_risk(rets: pd.DataFrame, weights: np.ndarray, bench: pd.Series) -> dict | None:
    """Risk of holding the picks together (review 2026-11-14, risk-1).

    rets: daily returns (dates x picks, in the investor's currency); weights sum to 1; bench: the
    market's daily returns. Volatility and correlation from the last 60 sessions, beta from 120.
    The one-month 95% loss line is -1.645 sigma sqrt(21/252): an estimate from past volatility,
    not a floor - early in a sell-off recent volatility understates what follows."""
    rets = rets.dropna(how="all").fillna(0.0)
    if len(rets) < 60 or not len(weights):
        return None
    r60 = rets.tail(60)
    cov = r60.cov().to_numpy() * 252
    vol = float(np.sqrt(weights @ cov @ weights))
    port = rets.tail(120) @ weights
    b = bench.reindex(port.index).fillna(0.0)
    beta = float(np.cov(port, b)[0, 1] / np.var(b, ddof=1)) if np.var(b) > 0 else None
    corr = r60.corr().to_numpy()
    n = len(weights)
    avg_corr = float((corr.sum() - np.trace(corr)) / (n * (n - 1))) if n > 1 else None
    return {"n": n, "vol": round(vol, 4), "beta": None if beta is None else round(beta, 2),
            "loss95_1m": round(float(-1.645 * vol * np.sqrt(21 / 252)), 4),
            "avg_corr": None if avg_corr is None else round(avg_corr, 2)}
