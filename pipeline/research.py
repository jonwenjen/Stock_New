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


def worst_windows(close: pd.Series, n: int = 5, days: int = 20, gap: int = 40) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """The n worst `days`-session returns of a series, no two ending within `gap` sessions of each other."""
    close = close.dropna()
    r = (close / close.shift(days) - 1).dropna().sort_values()
    picked: list[int] = []
    pos = {d: i for i, d in enumerate(close.index)}
    for d in r.index:
        i = pos[d]
        if all(abs(i - j) >= gap for j in picked):
            picked.append(i)
        if len(picked) == n:
            break
    return [(close.index[i - days], close.index[i]) for i in sorted(picked)]


def stress_replay(weights: dict[str, float], closes: dict[str, pd.Series], bench: pd.Series, index: pd.Series,
                  n: int = 5, days: int = 20) -> list[dict]:
    """Historical replay (review 2026-11-14, risk-8): today's picks through the market's worst 20-session
    stretches of the last five years. A pick without prices back then counts as beta x index return.
    The last 60 sessions are left out: today's picks were chosen partly for how they did then, so
    replaying that stretch would flatter them."""
    windows = worst_windows(bench.iloc[:-60].tail(1200), n, days)
    ir = index.pct_change()
    out = []
    for a, z in windows:
        port = 0.0
        for sym, w in weights.items():
            c = closes.get(sym)
            if c is not None and a in c.index and z in c.index and c[a] > 0:
                r = float(c[z] / c[a] - 1)
            else:
                beta = 1.0
                if c is not None and len(c) > 120:
                    sr = c.pct_change()
                    j = pd.concat([sr, ir], axis=1, sort=True).dropna().tail(250)
                    if len(j) > 60 and j.iloc[:, 1].var() > 0:
                        beta = float(j.cov().iloc[0, 1] / j.iloc[:, 1].var())
                ia, iz = index.asof(a), index.asof(z)
                r = beta * float(iz / ia - 1) if ia and iz else 0.0
            port += w * r
        out.append({"from": a.strftime("%Y-%m-%d"), "to": z.strftime("%Y-%m-%d"),
                    "bench": round(float(bench[z] / bench[a] - 1), 4), "picks": round(port, 4)})
    return out


def valuation_band(hist: dict[str, list], pe: float | None, pb: float | None, min_months: int = 24) -> dict | None:
    """Where today's P/E and P/B sit in the company's own month-end history (review 2026-11-14, fund-3).
    Returns percentiles (0-1) and the [low, median, high] of each history; losses (no P/E) are skipped."""
    out = {}
    for key, now, idx in (("pe", pe, 0), ("pb", pb, 1)):
        vals = sorted(v[idx] for v in hist.values() if v and len(v) > idx and v[idx] and v[idx] > 0)
        if now and now > 0 and len(vals) >= min_months:
            out[f"{key}_pct"] = round(sum(v <= now for v in vals) / len(vals), 3)
            out[f"{key}_band"] = [round(vals[0], 2), round(vals[len(vals) // 2], 2), round(vals[-1], 2)]
    return out or None


# Tests registered before their data exists (review 2026-11-14). The rules are fixed here and are not
# tuned after the data comes in; each is judged once, when its data is complete.
PREREGISTERED = [
    {"id": "futures_d20", "name": "台指期法人部位的 20 日變化",
     "hypothesis": "外資、投信、三大法人合計的台指期淨未平倉 20 日變化，和加權指數之後 5 日、20 日的報酬或 20 日內最大回檔有關。",
     "rule": "三條序列各自計算 Spearman 相關與 Newey-West t；|t| ≥ 2 而且前後兩段方向一致才算通過，通過的才可能回到風險分數。",
     "data": "期交所三大法人期貨部位，回補到約 3 年", "judge": "2027 年 1 月，只判定一次"},
    {"id": "chips_weight", "name": "籌碼分數的權重（目前 0.25）",
     "hypothesis": "外資與投信的個股買賣超分數，能排序之後一週與一個月的相對報酬。",
     "rule": "訊號隔一個交易日開盤才進場；樣本外 IC ≤ 0 → 權重改為 0；0 到 0.02 → 0.05；≥ 0.02 且 t ≥ 2 → 維持。投信項另外檢驗。",
     "data": "個股三大法人買賣超（T86），回補一年", "judge": "一年資料回補完成後"},
    {"id": "fund_weight", "name": "基本面分數的權重（目前 0.15）",
     "hypothesis": "月營收成長等基本面項目，能排序之後一個月與一季的相對報酬。",
     "rule": "營收在公布截止日（次月 10 日）之後才可使用；樣本外 IC ≤ 0 → 權重 0；0 到 0.02 → 0.05；≥ 0.02 且 t ≥ 2 → 維持。",
     "data": "月營收回補 5 年", "judge": "5 年月營收回補完成後"},
    {"id": "pit_backtest", "name": "以當時名單重做回測與校準",
     "hypothesis": "排名在當時成交值前 90 名的股票中，仍然有跑贏中位數的區分力。",
     "rule": "只用每個日期當時的前 90 名，評估實際排名用的分數；結果取代目前以今日名單算出的上限。",
     "data": "每日成交值前 150 名與全部上市股收盤價，回補到約 2.5 年", "judge": "存檔滿 2 年後"},
    {"id": "watch_first", "name": "先觀察旗標的區分力",
     "hypothesis": "有先觀察旗標的精選，之後 20 日的最大回檔比沒有旗標的大。",
     "rule": "比較兩組之後 20 日的報酬與最大回檔；差異的 t ≥ 2 才算有用。條件不再修改。",
     "data": "每週精選名單與旗標存檔", "judge": "累積 52 週之後（2027 年 11 月）"},
]
