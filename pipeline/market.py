"""Market safety: breadth index, regime, composite risk score, cross-market linkage,
forecast accuracy and rule-based alerts.

Everything here is a pure function over series the build already holds, so it is
tested without the network. The cut-offs are common rules of thumb; `bucket_stats`
and `calibration` exist to show how well they and the scores held up in the past.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from . import http

HORIZON_DAYS = {"1w": 5, "1m": 21, "1q": 63}  # same keys and lengths as scoring.HORIZONS
SCORE_EDGES = [-1.01, -0.45, -0.15, 0.15, 0.45, 1.01]  # the same cuts the labels use
SCORE_LABELS = ["強勢偏空", "偏空", "中性", "偏多", "強勢偏多"]


def lin(x, safe: float, danger: float):
    """Map a reading to 0 (at `safe`) .. 100 (at `danger`). Works on floats and Series."""
    return np.clip((x - safe) / (danger - safe), 0.0, 1.0) * 100.0


# ----------------------------- fetchers ----------------------------- #
http.HOST_INTERVAL.setdefault("www.taifex.com.tw", 2.0)


def parse_futures_csv(text: str) -> dict[str, dict]:
    """TAIFEX institutional futures download -> date -> foreign investors' TX net position."""
    out = {}
    for line in text.splitlines()[1:]:
        c = [x.strip() for x in line.split(",")]
        if len(c) >= 14 and c[1] == "臺股期貨" and c[2].startswith("外資"):
            out[c[0].replace("/", "-")] = {"net_oi": float(c[13]), "net_trade": float(c[7])}
    return out


def futures_history(start: str, end: str) -> dict[str, dict]:
    """Foreign TX net open interest per day between two ISO dates (the site serves about a month per request)."""
    r = http.request("POST", "https://www.taifex.com.tw/cht/3/futContractsDateDown",
                     data={"queryStartDate": start.replace("-", "/"), "queryEndDate": end.replace("-", "/"),
                           "commodityId": "TXF"})
    return parse_futures_csv(r.content.decode("big5", errors="replace"))


def ofr_fsi() -> pd.DataFrame:
    """OFR Financial Stress Index (33 market variables; 0 = long-run average stress) with its five categories."""
    txt = http.get_text("https://www.financialresearch.gov/financial-stress-index/data/fsi.csv")
    df = pd.read_csv(io.StringIO(txt), parse_dates=["Date"]).set_index("Date").sort_index()
    return df.rename(columns={"OFR FSI": "fsi", "Credit": "credit", "Equity valuation": "equity",
                              "Safe assets": "safe", "Funding": "funding", "Volatility": "vol"})


# ----------------------------- breadth ----------------------------- #
def breadth_index(b: pd.DataFrame, ad_ratio: float | None = None) -> pd.Series:
    """0..100: share of stocks above their 20- and 60-day averages, equally weighted.

    When a 10-day advance/decline ratio is given it nudges the latest value only
    (ratio 0.6 -> 0, 1.6 -> 100, weight 20%), since its history is short.
    """
    idx = (b["above20"] + b["above60"]) / 2 * 100
    if ad_ratio is not None and len(idx):
        idx = idx.copy()
        idx.iloc[-1] = 0.8 * idx.iloc[-1] + 0.2 * float(lin(ad_ratio, 0.6, 1.6))
    return idx.round(1)


def breakout_check(close: pd.Series, bidx: pd.Series, n: int = 20) -> dict | None:
    """Is a new high / low in the index confirmed by breadth?

    kind: "confirmed" (index at an n-day high with breadth >= 60 and not below where it was
    n days ago), "false_risk" (index at a high but breadth < 50 or falling), "washout"
    (index at an n-day low with breadth <= 20), "divergence_up" (index at a low while
    breadth is higher than n days ago), else "none".
    """
    df = pd.concat([close.rename("c"), bidx.rename("b")], axis=1).dropna()
    if len(df) < n + 1:
        return None
    c, b = df["c"], df["b"]
    win = c.tail(n)
    at_high = bool(c.iloc[-1] >= win.max() * 0.99)
    at_low = bool(c.iloc[-1] <= win.min() * 1.01)
    b_now, b_ago = float(b.iloc[-1]), float(b.iloc[-n - 1])
    if at_high and not at_low:
        kind = "confirmed" if b_now >= 60 and b_now >= b_ago - 5 else "false_risk" if b_now < 50 or b_now < b_ago - 10 else "none"
    elif at_low and not at_high:
        kind = "washout" if b_now <= 20 else "divergence_up" if b_now > b_ago + 5 else "none"
    else:
        kind = "none"
    return {"kind": kind, "at_high": at_high, "at_low": at_low, "breadth": b_now, "breadth_ago": b_ago, "n": n}


# ----------------------------- regime ----------------------------- #
REGIMES = {"bull": "多頭趨勢", "range": "高檔盤整", "pullback": "多頭回檔", "rebound": "空頭反彈", "bear": "空頭趨勢"}
REGIME_RISK = {"bull": 10.0, "range": 40.0, "pullback": 55.0, "rebound": 65.0, "bear": 90.0}


def regime_states(close: pd.Series, confirm: int = 3) -> pd.Series:
    """Classify each day from the 60- and 200-day averages.

    bull: above both with the 60-day rising; range: above both, 60-day flat or falling;
    pullback: above the 200-day, below the 60-day; rebound: below the 200-day, above the
    60-day; bear: below both. A new state must hold `confirm` days in a row to count,
    so a single close across an average does not flip the regime.
    """
    close = close.dropna()
    ma60, ma200 = close.rolling(60).mean(), close.rolling(200).mean()
    rising = ma60 > ma60.shift(20)
    a200, a60 = close > ma200, close > ma60
    raw = pd.Series("bear", index=close.index, dtype=object)
    raw[a200 & a60 & rising] = "bull"
    raw[a200 & a60 & ~rising] = "range"
    raw[a200 & ~a60] = "pullback"
    raw[~a200 & a60] = "rebound"
    raw = raw[ma200.notna()]
    out, cur, run, prev = [], None, 0, None
    for v in raw:
        run = run + 1 if v == prev else 1
        prev = v
        if cur is None or (v != cur and run >= confirm):
            cur = v
        out.append(cur)
    return pd.Series(out, index=raw.index, dtype=object)


def regime_now(states: pd.Series) -> dict | None:
    if states.empty:
        return None
    cur = states.iloc[-1]
    changed = states != states.shift(1)
    start = states.index[changed.values][-1]
    before = states[states.index < start]
    return {"state": cur, "name": REGIMES[cur], "since": start.strftime("%Y-%m-%d"),
            "days": int((states.index >= start).sum()),
            "prev": before.iloc[-1] if len(before) else None,
            "prev_name": REGIMES[before.iloc[-1]] if len(before) else None}


def forward_stats(groups: pd.Series, close: pd.Series) -> dict:
    """For each group label: how the index did over the next week / month / quarter of sessions.

    Samples overlap (every day is one), so n overstates the independent evidence.
    """
    close = close.dropna()
    out = {}
    for g in groups.dropna().unique():
        days = groups.index[groups == g]
        row = {"share": round(len(days) / len(groups), 3)}
        for h, d in HORIZON_DAYS.items():
            fwd = (close.shift(-d) / close - 1).reindex(days).dropna()
            row[h] = {"n": int(len(fwd)), "up": round(float((fwd > 0).mean()), 3),
                      "avg": round(float(fwd.mean()), 4)} if len(fwd) >= 10 else None
        out[str(g)] = row
    return out


# ----------------------------- cross-market linkage ----------------------------- #
def align(driver: pd.Series, index: pd.Index, prior: bool) -> pd.Series:
    """Driver value known at each local session. prior=True takes the last close strictly
    before that date (a US close precedes the next Taipei open)."""
    s = driver.dropna()
    full = s.reindex(s.index.union(index)).ffill()
    return (full.shift(1) if prior else full).reindex(index)


def linkage(tw: pd.Series, drivers: list[tuple[str, str, pd.Series, bool]], n: int = 120) -> dict | None:
    """How TAIEX daily moves have lined up with each driver's move over the last n sessions.

    Per driver: correlation, single-factor beta, its latest aligned move and what that
    move implies. `pending` is any driver move newer than the last local session, and
    `implied_next` applies the joint (multi-factor) betas to it.
    """
    tw = tw.dropna()
    cols, rows, pend = {"tw": tw.pct_change()}, [], {}
    for key, _name, s, prior in drivers:
        a = align(s, tw.index, prior)
        cols[key] = a.pct_change()
        last = s.dropna()
        pend[key] = float(last.iloc[-1] / a.iloc[-1] - 1) if len(last) and a.iloc[-1] else 0.0
    df = pd.DataFrame(cols).dropna().tail(n)
    if len(df) < 40:
        return None
    y = df["tw"].to_numpy()
    for key, name, _s, prior in drivers:
        x = df[key].to_numpy()
        var = float(np.var(x))
        beta = float(np.cov(x, y, bias=True)[0, 1] / var) if var else 0.0
        corr = float(np.corrcoef(x, y)[0, 1]) if var else 0.0
        long = pd.DataFrame(cols).dropna().tail(250)
        rows.append({"key": key, "name": name, "prior": prior, "corr": round(corr, 3),
                     "corr_250": round(float(long["tw"].corr(long[key])), 3) if len(long) >= 120 else None,
                     "beta": round(beta, 3), "last_move": round(float(x[-1]), 4),
                     "contrib": round(beta * float(x[-1]), 4), "pending": round(pend[key], 4)})
    X = np.column_stack([df[k].to_numpy() for k, *_ in drivers] + [np.ones(len(df))])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    r2 = 1 - float(resid.var() / y.var()) if y.var() else 0.0
    implied = float(sum(c * pend[k] for c, (k, *_) in zip(coef[:-1], drivers)))
    return {"n": int(len(df)), "r2": round(r2, 3), "drivers": rows, "implied_next": round(implied, 4),
            "has_pending": any(abs(v) > 1e-9 for v in pend.values()),
            "last_tw": round(float(y[-1]), 4), "explained_last": round(float((X @ coef)[-1]), 4)}


# ----------------------------- composite risk score ----------------------------- #
# key, name, weight. The total is a weighted mean over whichever components have data.
# The OFR stress index is built from credit spreads and volatility (among others), both of which
# already have a component here, so it is shown for context but no longer counted a third time;
# its weight went to dollar liquidity, which none of the other components measures.
RISK_PARTS = [("trend", "趨勢與週期", 20), ("breadth", "市場廣度", 15), ("foreign", "外資現貨", 10),
              ("futures", "外資期貨", 10), ("margin", "融資槓桿", 10), ("vol", "波動率", 10),
              ("credit", "信用利差", 10), ("intl", "國際連動", 15), ("liquidity", "美元流動性", 10)]
RISK_LEVELS = [(30, "低", "s1", "七成以上"), (50, "中等", "s0", "五至七成"),
               (70, "偏高", "s-1", "三至五成"), (101, "高", "s-2", "三成以下")]


def risk_level(score: float) -> dict:
    for cut, name, tone, exposure in RISK_LEVELS:
        if score < cut:
            return {"name": name, "tone": tone, "exposure": exposure}
    return {"name": "高", "tone": "s-2", "exposure": "三成以下"}


def intl_risk(soxx_r20, ndq_r20, fx_r20):
    """SOX and Nasdaq 20-day returns plus USD/TWD 20-day change (a weaker TWD is risk)."""
    return (lin(soxx_r20, 0.08, -0.12) + lin(ndq_r20, 0.05, -0.08) + lin(fx_r20, -0.02, 0.03)) / 3


def vol_risk(vix, term=None):
    base = lin(vix, 14.0, 32.0)
    return base if term is None else np.minimum(100.0, base + np.where(np.asarray(term) >= 1.0, 15.0, 0.0))


def credit_risk(oas, chg10=None):
    base = lin(oas, 3.0, 6.0)
    return base if chg10 is None else np.minimum(100.0, base + lin(chg10, 0.0, 1.0) * 0.3)


def liquidity_risk(chg13w):
    """Fed net liquidity over 13 weeks: +3% or more is safe, -5% or worse is danger."""
    return lin(chg13w, 0.03, -0.05)


def risk_parts(x: dict) -> list[dict]:
    """Score each component 0 (safe) .. 100 (danger) from today's readings; skip the ones without data."""
    g = x.get
    vals = {
        "trend": None if g("regime") is None else min(100.0, REGIME_RISK[g("regime")] + float(lin(g("drawdown") or 0, -0.05, -0.25)) * 0.2),
        "breadth": None if g("breadth") is None else 100.0 - g("breadth"),
        "foreign": None if g("foreign20_ratio") is None else min(100.0, float(lin(g("foreign20_ratio"), 0.02, -0.06))
                                                               + (10.0 if (g("foreign_streak") or 0) <= -5 else 0.0)),
        "futures": None if g("fut_pct") is None else (1.0 - g("fut_pct")) * 100.0,
        "margin": None if g("maint") is None else float(lin(g("maint"), 1.70, 1.40)),
        "vol": None if g("vix") is None else float(vol_risk(g("vix"), g("vix_term"))),
        "credit": None if g("oas") is None else float(credit_risk(g("oas"), g("oas_chg10"))),
        "intl": None if None in (g("soxx_r20"), g("ndq_r20"), g("fx_r20")) else float(intl_risk(g("soxx_r20"), g("ndq_r20"), g("fx_r20"))),
        "liquidity": None if g("liq_chg13") is None else float(liquidity_risk(g("liq_chg13"))),
    }
    return [{"key": k, "name": name, "weight": w, "risk": round(vals[k], 1)}
            for k, name, w in RISK_PARTS if vals[k] is not None]


def risk_total(parts: list[dict]) -> float | None:
    tot = sum(p["weight"] for p in parts)
    return round(sum(p["weight"] * p["risk"] for p in parts) / tot, 1) if tot else None


def risk_history(series: dict[str, pd.Series], index: pd.Index) -> pd.Series:
    """Rebuild the score for past days from the components that have long histories.

    series: component key -> daily risk (0..100). Each day uses the components that
    exist for it, weighted as in RISK_PARTS; days with under three components are dropped.
    """
    w = {k: wt for k, _n, wt in RISK_PARTS}
    df = pd.DataFrame({k: s.reindex(s.index.union(index)).ffill().reindex(index) for k, s in series.items()})
    weights = pd.DataFrame({k: df[k].notna() * w[k] for k in df.columns})
    score = (df.fillna(0) * weights).sum(axis=1) / weights.sum(axis=1)
    return score[df.notna().sum(axis=1) >= 3].round(1)


def bucket_stats(score: pd.Series, close: pd.Series) -> list[dict]:
    """Forward index returns after days grouped by risk level - the score's own track record."""
    names = [lv[1] for lv in RISK_LEVELS]
    groups = pd.cut(score, [-0.1] + [lv[0] for lv in RISK_LEVELS], labels=names, right=False).astype(object)
    stats = forward_stats(groups, close)
    return [{"level": n, **stats[n]} for n in names if n in stats]


# ----------------------------- forecast accuracy ----------------------------- #
def p_up(score: float, slope: float = 0.22) -> float:
    return float(np.clip(0.5 + slope * score, 0.28, 0.72))


def _pairs(frames: dict[str, pd.DataFrame], h: str, days: int, lookback: int, step: int) -> pd.DataFrame:
    """(date, score, forward return) for every stock on every step-th day of the look-back window."""
    scores = pd.DataFrame({s: f[f"t_{h}"] for s, f in frames.items()})
    closes = pd.DataFrame({s: f["close"] for s, f in frames.items()})
    fwd = closes.shift(-days) / closes - 1
    dates = scores.index[-lookback:-days:step] if len(scores) > days + 50 else []
    df = pd.concat([scores.loc[dates].stack().rename("s"), fwd.loc[dates].stack().rename("r")], axis=1).dropna()
    df["up"] = (df["r"] > 0).astype(float)
    return df


def _slope(df: pd.DataFrame) -> float:
    """Least-squares slope of (rose or not) on the score, comparing stocks within the same day.

    Both sides are demeaned per date first. Otherwise a rising market, in which most
    stocks go up and most scores are positive, would look like skill: the slope must
    come from higher-scored stocks doing better than lower-scored ones on the same day.
    """
    g = df.groupby(level=0)
    s, u = df["s"] - g["s"].transform("mean"), df["up"] - g["up"].transform("mean")
    den = float((s ** 2).sum())
    return float((s * u).sum() / den) if den else 0.0


def fit_slopes(frames: dict[str, pd.DataFrame], horizons: dict[str, int], floor: float = 0.03, cap: float = 0.22,
               lookback: int = 750, step: int = 5) -> dict:
    """How much up-probability a unit of score has actually been worth, per horizon.

    The site's probability is 50% + slope x score. `fit` is the slope the last ~3 years
    support; `k` is that value kept within [floor, cap], which is what gets published.
    Walk-forward check: the window is cut into five consecutive blocks and each of the
    last four is predicted with a slope fitted on the blocks before it only, giving the
    out-of-sample Brier score of the calibrated probability (`brier`) next to that of the
    original fixed slope (`brier_fixed`) and of always saying 50% (`brier_coin`).
    """
    out = {}
    for h, days in horizons.items():
        df = _pairs(frames, h, days, lookback, step)
        if len(df) < 500:
            out[h] = None
            continue
        dates = sorted(df.index.get_level_values(0).unique())
        cuts = [dates[int(len(dates) * i / 5)] for i in range(1, 5)]
        d0 = df.index.get_level_values(0)
        cal, fixed, coin, n = 0.0, 0.0, 0.0, 0
        for i, c in enumerate(cuts):
            end = cuts[i + 1] if i + 1 < len(cuts) else None
            train, test = df[d0 < c], df[(d0 >= c) & ((d0 < end) if end is not None else True)]
            if len(train) < 200 or test.empty:
                continue
            k = min(cap, max(0.0, _slope(train)))
            cal += float(((np.clip(0.5 + k * test["s"], 0.28, 0.72) - test["up"]) ** 2).sum())
            fixed += float(((np.clip(0.5 + cap * test["s"], 0.28, 0.72) - test["up"]) ** 2).sum())
            coin += float(((0.5 - test["up"]) ** 2).sum())
            n += len(test)
        fit = _slope(df)
        out[h] = {"k": round(min(cap, max(floor, fit)), 4), "fit": round(fit, 4), "n": int(len(df)),
                  "brier": round(cal / n, 4) if n else None, "brier_fixed": round(fixed / n, 4) if n else None,
                  "brier_coin": round(coin / n, 4) if n else None}
    return out


def calibration(frames: dict[str, pd.DataFrame], horizons: dict[str, int], lookback: int = 750, step: int = 5,
                slopes: dict[str, float] | None = None) -> dict:
    """Does a higher technical score go with a higher chance of rising? Past ~3 years.

    For each horizon the (score, forward return) pairs are grouped by the label cuts;
    hit is the share of non-neutral scores whose sign matched the outcome; brier is the
    mean squared error of the stated probability, base_brier that of always quoting the
    overall up-rate (lower is better; brier >= base_brier means no skill).
    """
    out = {}
    for h, days in horizons.items():
        scores = pd.DataFrame({s: f[f"t_{h}"] for s, f in frames.items()})
        closes = pd.DataFrame({s: f["close"] for s, f in frames.items()})
        fwd = closes.shift(-days) / closes - 1
        dates = scores.index[-lookback:-days:step] if len(scores) > days + 50 else []
        s = scores.loc[dates].stack()
        r = fwd.loc[dates].stack()
        df = pd.concat([s.rename("s"), r.rename("r")], axis=1).dropna()
        if len(df) < 200:
            out[h] = None
            continue
        df["up"] = (df["r"] > 0).astype(float)
        df["p"] = np.clip(0.5 + (slopes or {}).get(h, 0.22) * df["s"], 0.28, 0.72)
        base = float(df["up"].mean())
        active = df[df["s"].abs() >= 0.15]
        bins = []
        for i, name in enumerate(SCORE_LABELS):
            b = df[(df["s"] > SCORE_EDGES[i]) & (df["s"] <= SCORE_EDGES[i + 1])]
            bins.append({"label": name, "n": int(len(b)),
                         "up": round(float(b["up"].mean()), 3) if len(b) else None,
                         "p": round(float(b["p"].mean()), 3) if len(b) else None,
                         "avg": round(float(b["r"].mean()), 4) if len(b) else None})
        out[h] = {"n": int(len(df)), "base_up": round(base, 3),
                  "hit": round(float((np.sign(active["s"]) == np.sign(active["r"])).mean()), 3) if len(active) else None,
                  "brier": round(float(((df["p"] - df["up"]) ** 2).mean()), 4),
                  "base_brier": round(float(((base - df["up"]) ** 2).mean()), 4), "bins": bins}
    return out


def evaluate_log(log: dict[str, dict], frames: dict[str, pd.DataFrame], horizons: dict[str, int],
                 slopes: dict[str, float] | None = None) -> dict:
    """Score the forecasts this site actually published.

    log: data date -> {symbol: [close, score_1w, score_1m, score_1q]}. A forecast counts
    once its horizon has fully elapsed in the price history. Returns per horizon the
    number of matured forecasts and snapshots, hit rate of non-neutral calls, Brier
    score against the base rate, and the mean return of bullish versus bearish calls.
    """
    out = {}
    for i, (h, days) in enumerate(horizons.items()):
        rows, snaps = [], 0
        for date, syms in sorted(log.items()):
            matured = False
            for sym, v in syms.items():
                f = frames.get(sym)
                if f is None or v[1 + i] is None:
                    continue
                pos = int(f.index.searchsorted(pd.Timestamp(date), side="right")) - 1  # last bar on or before the data date
                if pos < 0 or pos + days >= len(f):
                    continue
                c = f["close"]
                rows.append((v[1 + i], float(c.iloc[pos + days] / c.iloc[pos] - 1)))
                matured = True
            snaps += matured
        if not rows:
            out[h] = {"n": 0, "snapshots": 0}
            continue
        s, r = np.array([x[0] for x in rows]), np.array([x[1] for x in rows])
        up, p = (r > 0).astype(float), np.clip(0.5 + (slopes or {}).get(h, 0.22) * s, 0.28, 0.72)
        act, bull, bear = np.abs(s) >= 0.15, s >= 0.15, s <= -0.15
        base = float(up.mean())
        out[h] = {"n": int(len(rows)), "snapshots": int(snaps), "base_up": round(base, 3),
                  "hit": round(float((np.sign(s[act]) == np.sign(r[act])).mean()), 3) if act.any() else None,
                  "brier": round(float(((p - up) ** 2).mean()), 4),
                  "base_brier": round(float(((base - up) ** 2).mean()), 4),
                  "bull_ret": round(float(r[bull].mean()), 4) if bull.any() else None,
                  "bear_ret": round(float(r[bear].mean()), 4) if bear.any() else None,
                  "bull_n": int(bull.sum()), "bear_n": int(bear.sum())}
    return out


def evaluate_index_log(log: dict[str, dict], frames: dict[str, pd.DataFrame], horizons: dict[str, int]) -> list[dict]:
    """Each matured index forecast against what happened: direction and whether the range held.

    log: data date -> {symbol: {"close": c, "h": {horizon: [p_up, low, high]}}}.
    """
    out = []
    for date, syms in sorted(log.items()):
        for sym, rec in syms.items():
            f = frames.get(sym)
            if f is None:
                continue
            pos = int(f.index.searchsorted(pd.Timestamp(date), side="right")) - 1
            for h, days in horizons.items():
                fc = (rec.get("h") or {}).get(h)
                if not fc or pos < 0 or pos + days >= len(f):
                    continue
                actual = float(f["close"].iloc[pos + days])
                ret = actual / float(f["close"].iloc[pos]) - 1
                out.append({"date": date, "symbol": sym, "h": h, "p_up": fc[0], "low": fc[1], "high": fc[2],
                            "actual": round(actual, 2), "ret": round(ret, 4),
                            "dir_ok": None if abs(fc[0] - 0.5) < 0.033 else bool((fc[0] > 0.5) == (ret > 0)),
                            "in_range": bool(fc[1] <= actual <= fc[2])})
    return out


# ----------------------------- stocks gathering or losing momentum ----------------------------- #
def momentum_lists(records: list[dict], tactical_prev: dict[str, float], n: int = 12) -> tuple[list[dict], list[dict]]:
    """Two screens over the scored universe.

    accumulating: not yet extended, with several early signs lining up (price back above
    its 20-day average, 20-day gain under 15%, RSI 45-68, steady institutional buying,
    short-term score rising week on week, sound fundamentals).
    weakening: still above the 60-day average or rated neutral-or-better, but short-term
    signs are turning (score falling, below the 20-day average, overbought and slipping,
    foreign selling streak, margin debt rising into a falling price, momentum lagging trend).
    Each entry lists the conditions it met; at least four (accumulating) or three (weakening).
    """
    acc, weak = [], []
    for r in records:
        t, prev = r.get("tactical"), tactical_prev.get(r["symbol"])
        if t is None or not r.get("ma20") or not r.get("ma60"):
            continue
        d = None if prev is None else t - prev
        f = r.get("factors") or {}
        streak, rsi, r20, c5 = r.get("foreign_streak") or 0, r.get("rsi") or 50, r.get("r20") or 0, r.get("chg5d") or 0
        ups = [("站上月線", r["close"] > r["ma20"]), ("月漲幅未過熱", 0 <= r20 < 0.15), ("RSI 適中", 45 <= rsi <= 68),
               ("法人連買", streak >= 3 or (r.get("trust5") or 0) > 0 and (r.get("foreign5") or 0) > 0),
               ("短線分數上升", d is not None and d >= 0.1),
               ("結構面佳", (r.get("structural") or 0) >= 0.2 or bool(r.get("rev_rising")))]
        tags = [k for k, ok in ups if ok]
        if t > 0 and len(tags) >= 4:
            acc.append({**_brief(r, d), "tags": tags})
        downs = [("短線分數下滑", d is not None and d <= -0.15), ("跌破月線", r["close"] < r["ma20"]),
                 ("超買後回落", rsi >= 68 and c5 < 0), ("外資連賣", streak <= -3),
                 ("融資增、股價跌", (r.get("margin_chg5") or 0) > 0.05 and c5 < 0),
                 ("動能落後趨勢", (f.get("momentum") if f.get("momentum") is not None else 0)
                  < (f.get("trend") if f.get("trend") is not None else 0) - 0.3)]
        tags = [k for k, ok in downs if ok]
        if (r["close"] > r["ma60"] or (r.get("overall") or 0) >= 0) and len(tags) >= 3:
            weak.append({**_brief(r, d), "tags": tags})
    acc.sort(key=lambda x: (-len(x["tags"]), -(x["tactical"] or 0)))
    weak.sort(key=lambda x: (-len(x["tags"]), x["tactical"] or 0))
    return acc[:n], weak[:n]


def _brief(r: dict, d: float | None) -> dict:
    return {"symbol": r["symbol"], "code": r["code"], "name": r["name"], "market": r["market"],
            "close": r["close"], "chg5d": r.get("chg5d"), "r20": r.get("r20"), "rsi": r.get("rsi"),
            "tactical": r.get("tactical"), "tactical_chg": None if d is None else round(d, 3),
            "overall": r.get("overall"), "p_up": {h: v["p_up"] for h, v in (r.get("horizons") or {}).items()}}


# ----------------------------- alerts ----------------------------- #
def alerts(x: dict) -> list[dict]:
    """Plain rules over today's readings. level 2 = warning, 1 = watch, 0 = improving."""
    g, out = x.get, []
    add = lambda lv, title, text: out.append({"level": lv, "title": title, "text": text})  # noqa: E731
    risk, prev = g("risk"), g("risk_prev")
    if risk is not None and risk >= 70:
        add(2, "市場風險分數進入高風險區", f"綜合風險分數 {risk:.0f}（滿分 100），多數指標同時偏弱。")
    if risk is not None and prev is not None and risk - prev >= 10:
        add(2, "風險分數快速上升", f"由上次的 {prev:.0f} 升到 {risk:.0f}。")
    if risk is not None and prev is not None and prev - risk >= 10:
        add(0, "風險分數明顯下降", f"由上次的 {prev:.0f} 降到 {risk:.0f}。")
    if g("regime") and g("regime_prev_run") and g("regime") != g("regime_prev_run"):
        worse = REGIME_RISK[g("regime")] > REGIME_RISK[g("regime_prev_run")]
        add(2 if worse else 0, "市場週期轉變", f"由「{REGIMES[g('regime_prev_run')]}」轉為「{REGIMES[g('regime')]}」。")
    elif g("regime") == "bear":
        add(2, "加權指數處於空頭趨勢", "指數同時在季線與年線之下。")
    bo = g("breakout") or {}
    if bo.get("kind") == "false_risk":
        add(2, "指數創高但廣度沒有跟上", f"加權指數接近 {bo['n']} 日高點，廣度指數只有 {bo['breadth']:.0f}（{bo['n']} 日前 {bo['breadth_ago']:.0f}），假突破風險高。")
    elif bo.get("kind") == "washout":
        add(1, "廣度進入超賣區", f"廣度指數 {bo['breadth']:.0f}，指數在 {bo['n']} 日低點附近；急跌尾聲常見，但需要止穩訊號確認。")
    elif bo.get("kind") == "divergence_up":
        add(0, "指數破底但廣度回升", f"廣度指數 {bo['breadth']:.0f} 高於 {bo['n']} 日前的 {bo['breadth_ago']:.0f}，賣壓集中在少數權值股。")
    elif bo.get("kind") == "confirmed":
        add(0, "指數創高且廣度同步", f"廣度指數 {bo['breadth']:.0f}，突破有多數個股支持。")
    if bo.get("breadth_ago") is not None and bo["breadth_ago"] - bo["breadth"] >= 30:
        add(1, "廣度一個月內大幅惡化", f"廣度指數由 {bo['n']} 日前的 {bo['breadth_ago']:.0f} 降到 {bo['breadth']:.0f}，多數個股已跌破均線。")
    b, bchg = g("breadth"), g("breadth_chg5")
    if b is not None and bchg is not None and bchg <= -15:
        add(1, "廣度快速惡化", f"廣度指數一週下降 {abs(bchg):.0f} 點，目前 {b:.0f}。")
    st = g("foreign_streak") or 0
    if st <= -5:
        add(2 if st <= -10 else 1, "外資連續賣超", f"外資已連續 {abs(st)} 日賣超。")
    elif st >= 5:
        add(0, "外資連續買超", f"外資已連續 {st} 日買超。")
    if g("fut_pct") is not None and g("fut_pct") <= 0.1:
        add(1, "外資台指期淨空單在一年高檔", f"淨未平倉 {g('fut_net'):+,.0f} 口，"
                + ("是過去一年最偏空的水準。" if g("fut_pct") <= 0.01 else f"比過去一年 {1 - g('fut_pct'):.0%} 的日子更偏空。"))
    if g("fut_chg5") is not None and g("fut_chg5") <= -15000:
        add(1, "外資期貨空單快速增加", f"淨未平倉一週變化 {g('fut_chg5'):+,.0f} 口。")
    if g("maint") is not None and g("maint") < 1.5:
        add(2 if g("maint") < 1.4 else 1, "融資維持率偏低", f"大盤融資維持率估算約 {g('maint'):.0%}，接近 130% 追繳線時容易出現斷頭賣壓。")
    if g("vix_term") is not None and g("vix_term") >= 1:
        add(2, "VIX 期限結構倒掛", f"VIX ÷ VIX3M = {g('vix_term'):.2f}，短期恐慌高於三個月後的預期。")
    elif g("vix") is not None and g("vix") >= 25:
        add(1, "VIX 偏高", f"VIX {g('vix'):.1f}。")
    if g("oas_chg10") is not None and g("oas_chg10") >= 0.5:
        add(2, "高收益債利差快速擴大", f"10 日擴大 {g('oas_chg10'):.2f} 個百分點，信用市場開始緊縮。")
    if g("soxx_r20") is not None and g("soxx_r20") <= -0.10:
        add(2 if g("soxx_r20") <= -0.15 else 1, "費城半導體明顯走弱", f"SOXX 近 20 日 {g('soxx_r20'):+.1%}，台股與半導體高度連動。")
    if g("ndq_r20") is not None and g("ndq_r20") <= -0.08:
        add(1, "那斯達克進入修正", f"近 20 日 {g('ndq_r20'):+.1%}。")
    if g("fx_r20") is not None and g("fx_r20") >= 0.02:
        add(1, "新台幣明顯走貶", f"美元／台幣近 20 日 {g('fx_r20'):+.1%}，外資匯出壓力升高。")
    if g("liq_chg13") is not None and g("liq_chg13") <= -0.03:
        add(1, "美元流動性收縮", f"Fed 淨流動性 13 週變化 {g('liq_chg13'):+.1%}，資金環境對風險資產轉為逆風。")
    if g("fsi") is not None and g("fsi") > 0:
        add(2, "全球金融壓力高於長期平均", f"OFR 金融壓力指數 {g('fsi'):+.2f}（0 為長期平均）。")
    elif g("fsi_chg20") is not None and g("fsi_chg20") >= 0.75:
        add(1, "全球金融壓力升溫", f"OFR 金融壓力指數 20 日上升 {g('fsi_chg20'):.2f}，目前 {g('fsi'):+.2f}。")
    return sorted(out, key=lambda a: -a["level"])
