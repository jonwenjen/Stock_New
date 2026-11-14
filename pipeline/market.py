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
    """TAIFEX institutional futures download -> date -> TX net open interest of foreign investors
    ("net_oi", plus "net_trade"), investment trusts ("trust_oi") and dealers ("dealer_oi").

    The trusts' long position is mostly the hedge of leveraged TAIEX ETFs, and the foreign short
    largely mirrors it, so the foreign figure alone overstates how bearish institutions are."""
    out: dict[str, dict] = {}
    for line in text.splitlines()[1:]:
        c = [x.strip() for x in line.split(",")]
        if len(c) >= 14 and c[1] == "臺股期貨":
            d = out.setdefault(c[0].replace("/", "-"), {})
            if c[2].startswith("外資"):
                d.update({"net_oi": float(c[13]), "net_trade": float(c[7])})
            elif c[2].startswith("投信"):
                d["trust_oi"] = float(c[13])
            elif c[2].startswith("自營"):
                d["dealer_oi"] = float(c[13])
    return {k: v for k, v in out.items() if "net_oi" in v}


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
# Only the bear state stood apart in five years of data (20-day drawdowns over 5%: bear 46%, the others
# 8-16%, in no useful order), so the trend part separates bear from everything else.
REGIME_RISK = {"bull": 20.0, "range": 20.0, "pullback": 20.0, "rebound": 20.0, "bear": 90.0}


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
# Review 2026-11-14: the foreign futures position left the score (it mirrors the trusts' leveraged-ETF
# hedge and correlates -0.9 with the index level); breadth went from 15 to 5 (no measurable information
# over five years). The total is renormalised over what remains.
RISK_PARTS = [("trend", "趨勢與週期", 20), ("breadth", "市場廣度", 5), ("foreign", "外資現貨", 10),
              ("margin", "融資槓桿", 10), ("vol", "波動率", 10),
              ("credit", "信用利差", 10), ("intl", "國際連動", 15), ("liquidity", "美元流動性", 10)]
RISK_LEVELS = [(30, "低", "s1", "七成以上"), (50, "中等", "s0", "五至七成"),
               (70, "偏高", "s-1", "三至五成"), (101, "高", "s-2", "三成以下")]
# The budget panel's default stock share for each level: a number, so it can only fall as risk rises.
EXPOSURE_PCT = {"低": 80, "中等": 60, "偏高": 40, "高": 20}


def risk_level(score: float) -> dict:
    for cut, name, tone, exposure in RISK_LEVELS:
        if score < cut:
            return {"name": name, "tone": tone, "exposure": exposure, "exposure_pct": EXPOSURE_PCT[name]}
    return {"name": "高", "tone": "s-2", "exposure": "三成以下", "exposure_pct": EXPOSURE_PCT["高"]}


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


def drawdown_odds(groups: pd.Series, close: pd.Series, days: int = 20, threshold: float = 0.05) -> dict:
    """For each group: how often the index fell more than `threshold` below the day's close at some
    point in the next `days` sessions. This is what the risk score can tell apart; returns it cannot."""
    close = close.dropna()
    low = close[::-1].rolling(days, min_periods=days).min()[::-1].shift(-1)  # lowest close of the next `days`
    hit = (low / close - 1 <= -threshold).where(low.notna())
    out = {}
    for g in groups.dropna().unique():
        h = hit.reindex(groups.index[groups == g]).dropna()
        if len(h) >= 10:
            out[str(g)] = {"n": int(len(h)), "dd": round(float(h.mean()), 3)}
    return out


def bucket_stats(score: pd.Series, close: pd.Series) -> list[dict]:
    """Forward index returns after days grouped by risk level - the score's own track record -
    plus the chance of a drawdown of more than 5% within 20 sessions."""
    names = [lv[1] for lv in RISK_LEVELS]
    groups = pd.cut(score, [-0.1] + [lv[0] for lv in RISK_LEVELS], labels=names, right=False).astype(object)
    stats, dd = forward_stats(groups, close), drawdown_odds(groups, close)
    return [{"level": n, **stats[n], "dd20": dd.get(n)} for n in names if n in stats]


# ----------------------------- forecast accuracy ----------------------------- #
def p_up(score: float, slope: float = 0.22) -> float:
    return float(np.clip(0.5 + slope * score, 0.28, 0.72))


def fit_relative(panel: pd.DataFrame, closes: pd.DataFrame, horizons: dict[str, int], cap: float = 0.25,
                 lookback: int = 750, step: int = 5) -> dict:
    """How well the published score has told stocks that beat their market from those that lagged.

    panel: dates x symbols, the score as a cross-sectional rank in [-1, 1]; closes: same shape.
    For every step-th date in the look-back, each stock "beat" if its forward return exceeded
    that date's median. The chance of beating is modelled as 50% + k x score (comparisons
    within a date, so a rising market cannot pass for skill).

    Walk-forward check: the window is cut into five consecutive blocks; each of the last four
    is predicted with k fitted on the blocks before it only. `edge` is True when that
    out-of-sample Brier score beats always saying 50%. `k` is the full-sample fit kept within
    [0, cap] - published as is, no floor. `base_up` is how often a stock rose at all over the
    horizon in this sample (it depends on the market's direction, so it is context, not a forecast).
    """
    out = {}
    for h, days in horizons.items():
        fwd = closes.shift(-days) / closes - 1
        dates = panel.index[-lookback:-days:step] if len(panel) > days + 50 else []
        x, r = panel.loc[dates].stack(), fwd.reindex(dates).stack()
        df = pd.concat([x.rename("x"), r.rename("r")], axis=1).dropna()
        if len(df) < 500:
            out[h] = None
            continue
        med = df.groupby(level=0)["r"].transform("median")
        df["beat"] = (df["r"] > med).astype(float)
        df["up"] = (df["r"] > 0).astype(float)
        fit = lambda d: float((d["x"] * (d["beat"] - 0.5)).sum() / max(1e-12, float((d["x"] ** 2).sum())))  # noqa: E731
        ds = sorted(df.index.get_level_values(0).unique())
        cuts = [ds[int(len(ds) * i / 5)] for i in range(1, 5)]
        d0 = df.index.get_level_values(0)
        model = coin = 0.0
        n = 0
        for i, c in enumerate(cuts):
            end = cuts[i + 1] if i + 1 < len(cuts) else None
            train, test = df[d0 < c], df[(d0 >= c) & ((d0 < end) if end is not None else True)]
            if len(train) < 200 or test.empty:
                continue
            k = min(cap, max(0.0, fit(train)))
            model += float(((np.clip(0.5 + k * test["x"], 0.3, 0.7) - test["beat"]) ** 2).sum())
            coin += float(((0.5 - test["beat"]) ** 2).sum())
            n += len(test)
        k_all = fit(df)
        bins = []
        for i, name in enumerate(SCORE_LABELS):
            b = df[(df["x"] > SCORE_EDGES[i]) & (df["x"] <= SCORE_EDGES[i + 1])]
            bins.append({"label": name, "n": int(len(b)),
                         "beat": round(float(b["beat"].mean()), 3) if len(b) else None,
                         "up": round(float(b["up"].mean()), 3) if len(b) else None,
                         "p": round(float(np.clip(0.5 + min(cap, max(0.0, k_all)) * b["x"], 0.3, 0.7).mean()), 3) if len(b) else None,
                         "avg": round(float(b["r"].mean()), 4) if len(b) else None})
        out[h] = {"k": round(min(cap, max(0.0, k_all)), 4), "fit": round(k_all, 4), "n": int(len(df)),
                  "brier": round(model / n, 4) if n else None, "brier_coin": round(coin / n, 4) if n else None,
                  "edge": bool(n and model < coin), "base_up": round(float(df["up"].mean()), 3), "bins": bins}
    return out


def p_beat(x: float, k: float) -> float:
    """Chance of beating the market's median stock for a score x in [-1, 1]."""
    return float(np.clip(0.5 + k * x, 0.3, 0.7))


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
               ("籌碼分數上升", d is not None and d >= 0.1),
               ("結構面佳", (r.get("structural") or 0) >= 0.2 or bool(r.get("rev_rising")))]
        tags = [k for k, ok in ups if ok]
        if t > 0 and len(tags) >= 4:
            acc.append({**_brief(r, d), "tags": tags})
        downs = [("籌碼分數下滑", d is not None and d <= -0.15), ("跌破月線", r["close"] < r["ma20"]),
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
            "overall": r.get("overall"), "rank": r.get("rank"),
            "p_beat": {h: v.get("p_beat") for h, v in (r.get("horizons") or {}).items()}}


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
        bc = g("breadth_chg5")
        add(1, "廣度一個月內大幅惡化", f"廣度指數由 {bo['n']} 日前的 {bo['breadth_ago']:.0f} 降到 {bo['breadth']:.0f}，多數個股已跌破均線"
            + (f"；但近 5 日 {bc:+.0f}，已在回升。" if bc is not None and bc >= 10 else "。"))
    b, bchg = g("breadth"), g("breadth_chg5")
    if b is not None and bchg is not None and bchg <= -15:
        add(1, "廣度快速惡化", f"廣度指數一週下降 {abs(bchg):.0f} 點，目前 {b:.0f}。")
    st = g("foreign_streak") or 0
    if st <= -5:
        add(2 if st <= -10 else 1, "外資連續賣超", f"外資已連續 {abs(st)} 日賣超。")
    elif st >= 5:
        add(0, "外資連續買超", f"外資已連續 {st} 日買超。")
    if g("fut_all_chg5") is not None and g("fut_all_chg5") <= -15000:
        add(1, "三大法人台指期淨部位快速轉空", f"外資＋投信＋自營商淨未平倉一週變化 {g('fut_all_chg5'):+,.0f} 口。")
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
