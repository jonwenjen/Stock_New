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
     "rule": "訊號隔一個交易日開盤才進場；樣本外 IC ≤ 0 → 權重改為 0；0 到 0.02 → 0.05；≥ 0.02 且 t ≥ 2 → 維持。投信項另外檢驗；自營商（自行買賣）與融資兩項也各自檢驗（2026-11-14d 加註，資料到齊前）。",
     "data": "個股三大法人買賣超（T86），回補一年", "judge": "一年資料回補完成後"},
    {"id": "fund_weight", "name": "基本面分數的權重（目前 0.15）",
     "hypothesis": "月營收成長等基本面項目，能排序之後一個月與一季的相對報酬。",
     "rule": "營收在公布截止日（次月 10 日）之後才可使用；樣本外 IC ≤ 0 → 權重 0；0 到 0.02 → 0.05；≥ 0.02 且 t ≥ 2 → 維持。檢驗宇宙固定為當時成交值前 150 名上市股＋上櫃前 30 名（2026-11-14d 在資料到齊前寫死：同一個營收項在流動股與觀察名單上的結果相反）。",
     "data": "月營收回補 5 年", "judge": "5 年月營收回補完成後"},
    {"id": "pit_backtest", "name": "以當時名單重做回測與校準",
     "hypothesis": "排名在當時成交值前 90 名的股票中，仍然有跑贏中位數的區分力。",
     "rule": "只用每個日期當時的前 90 名，評估實際排名用的分數；結果取代目前以今日名單算出的上限。",
     "data": "每日成交值前 150 名與全部上市股收盤價，回補到約 2.5 年", "judge": "存檔滿 2 年後"},
    {"id": "watch_first", "name": "先觀察旗標的區分力",
     "hypothesis": "有先觀察旗標的精選，之後 20 日的最大回檔比沒有旗標的大。",
     "rule": "比較兩組之後 20 日的報酬與最大回檔；差異的 t ≥ 2 才算有用。條件不再修改。",
     "data": "每週精選名單與旗標存檔", "judge": "累積 52 週之後（2027 年 11 月）"},
    # Added by the review of outside suggestions (2026-11-14d). Each is display-free until judged.
    {"id": "rs_high", "name": "相對 0050 比值線創 250 日新高",
     "hypothesis": "比值線創 250 日新高的股票，控制技術排名後，之後 20 日相對 0050 的超額較高。",
     "rule": "當時成交值前 90 名、隔一個交易日開盤進場；每週回歸「超額 ~ 旗標＋技術排名」，旗標係數 Newey-West t ≥ 2 且前後兩段同號才通過；通過也只成為權重 0 的標籤。",
     "data": "全部上市股收盤存檔滿 250 日之後再累積 52 週", "judge": "2028 年 1 月，只判定一次"},
    {"id": "podcast_drift", "name": "Podcast 首次提及之後的表現",
     "hypothesis": "28 天內第一次被追蹤節目提到的股票，之後 20 日相對 0050／SPY 的超額不為 0。",
     "rule": "只用 2026-11-18 起每週即時存檔的事件與當週名單；月叢集 |t| ≥ 2 且前後兩段同號為「有支持」，否則「無證據」。不論結果都不做節目權重；聲量權重只能在通過後另提。",
     "data": "每個市場 200 個即時事件（12 個節目約 1.5–2 年）", "judge": "到齊時判定一次，最晚 2028 年 11 月"},
    {"id": "regime_entry", "name": "空頭階段是否該停止加入新股",
     "hypothesis": "加權指數處於空頭趨勢或空頭反彈時，前 12 名之後 20 日的超額低於其他階段。",
     "rule": "用當時名單；20 日區塊自助法的 95% 區間不含 0，而且至少 3 段空頭（各 ≥ 20 個交易日），才考慮空頭時不加新股。",
     "data": "全部上市股收盤與當時成交值前 150 名的存檔", "judge": "存檔涵蓋 3 段空頭時判定一次，最晚 2029 年 11 月"},
    {"id": "chip_divergence", "name": "法人與股價背離（四種）",
     "hypothesis": "符合下列任一標記的股票，之後 20 日相對名單中位數的報酬與其他股票不同：①法人連 5 日賣超（合計 ≥ 0.15 日均量）而 5 日報酬不低於市場中位數；②法人連 5 日買超而收盤低於前 60 日高點；③集保 400 張以上 4 週增 ≥ 1 個百分點、50 張以下減 ≥ 1 個百分點；④融資 5 日減 ≥ 3% 而股價 5 日漲 > 1%。",
     "rule": "定義不再修改；隔一個交易日開盤進場，20 日為主、5 日參考；當日成交值前 150 名上市股；逐週組間差的 Newey-West t（lag 3），|t| ≥ 2 且前後兩半同號才算有證據。通過只加證據等級、不進分數；未通過就不顯示並公布結果。",
     "data": "個股法人一年、全部上市股收盤、個股融資一年、全市場集保 52 週", "judge": "①② 在法人資料滿 250 日後（約 2027 年 2 月），④ 在融資滿 250 日，③ 在集保滿 52 週（2027 年 11 月），各判定一次"},
    {"id": "rev_accel", "name": "營收新特徵（近 3 月年增、12 個月新高）",
     "hypothesis": "近 3 月營收年增與單月營收創 12 個月新高，在扣掉已計分的營收項後，仍能排序之後的相對報酬。",
     "rule": "新特徵排名對既有營收項回歸取殘差 IC；平均 ≥ 0.02、Newey-West t ≥ 2、前後兩半同號，才可取代月增項（0.10）。宇宙與時點同 fund_weight。",
     "data": "月營收回補 5 年", "judge": "與 fund_weight 同時判定"},
    {"id": "industry_flow", "name": "產業成交值占比上升之後",
     "hypothesis": "成交值占比上升的產業，之後 20 日的報酬中位數較高。",
     "rule": "每週跨產業 Spearman 相關；Newey-West t ≥ 2 且前後兩半同號，文案才可以提「資金流向」。",
     "data": "每週產業表存檔 104 週", "judge": "2028 年 11 月，只判定一次"},
    {"id": "valuation_pct", "name": "本益比位階與之後的回檔",
     "hypothesis": "本益比位於自身 5 年 90% 以上的股票，之後 60 日最大回檔大於位階 50% 以下的股票。",
     "rule": "兩組之後 60 日最大回檔的差，t ≥ 2 且前後兩半同號，才可列為先觀察條件。",
     "data": "80 檔以上、每檔 36 個月月底本益比", "judge": "資料齊全時判定一次"},
    {"id": "exdiv_entry", "name": "入選後 10 日內除權息",
     "hypothesis": "入選後 10 個交易日內除權息的精選，之後 20 日的相對報酬低於同週其他精選。",
     "rule": "隔一個交易日開盤進場、報酬含權息還原；逐週組間差的 Newey-West t，|t| ≥ 2 且前後兩半同號才列為先觀察條件。2026-11 的初步重演 t −2.3 只是約 16 個比較之一，不據以判定。",
     "data": "每週精選名單與除權息日存檔 52 週", "judge": "2027 年 11 月，只判定一次"},
]


# --------------------------------------------------------------------------- #
# Factor check for stock factors (review 2026-11-14d, C7 + C11)
# --------------------------------------------------------------------------- #
FACTOR_MIN_PERIODS, FACTOR_LONG_PERIODS = 24, 150


def nw_mean_t(x: np.ndarray, lag: int) -> float | None:
    """t of the mean of x with Newey-West (Bartlett) standard errors; lag 0 is the ordinary t."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return None
    u = x - x.mean()
    s = float((u * u).sum())
    for k in range(1, min(lag, len(u) - 1) + 1):
        s += 2 * (1 - k / (lag + 1)) * float((u[k:] * u[:-k]).sum())
    se = np.sqrt(s * len(x) / (len(x) - 1)) / len(x) if s > 0 else 0.0
    return float(x.mean() / se) if se > 0 else None


def _xs_spearman(a: pd.Series, b: pd.Series, min_names: int) -> float | None:
    idx = a.dropna().index.intersection(b.dropna().index)
    if len(idx) < min_names:
        return None
    ra, rb = a[idx].rank(), b[idx].rank()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(ra.corr(rb))


def factor_grade(ics: list[float], t: float | None) -> str:
    """A: 150+ weekly periods, |t| >= 2, same sign in both halves; B: |t| >= 2; C; D: under 24 periods."""
    n = len(ics)
    if n < FACTOR_MIN_PERIODS or t is None:
        return "D"
    if abs(t) < 2:
        return "C"
    h = n // 2
    same = np.sign(np.mean(ics[:h])) == np.sign(np.mean(ics[h:])) == np.sign(t)
    return "A" if n >= FACTOR_LONG_PERIODS and same else "B"


def factor_check(scores: dict[str, pd.DataFrame], opens: pd.DataFrame, closes: pd.DataFrame,
                 ref: pd.DataFrame | None = None, horizons: tuple = (5, 10, 20), lookback: int = 750,
                 step: int = 5, min_names: int = 20) -> dict[str, dict]:
    """Rank IC of each factor against later returns, the way the leading cards are graded.

    scores: name -> dates x symbols panel, known at the close of each date. The return runs from the
    open of the next session to the close of the h-th session (entry day counts as the first), so
    nothing is traded at a price that was known when the score was. Every `step` sessions over the
    last `lookback`; t is Newey-West with lag ceil(h / step) for the overlap. The grade uses the
    20-session horizon. corr_tech: the mean cross-sectional rank correlation with `ref` (the
    technical score as ranked) - a factor highly correlated with it says mostly the same thing."""
    idx = closes.index
    op = opens.reindex(index=idx, columns=closes.columns).where(lambda v: v > 0)
    fwd = {h: closes.shift(-h) / op.shift(-1) - 1 for h in horizons}
    dates = idx[-lookback:]
    out = {}
    for name, panel in scores.items():
        panel = panel.reindex(index=idx)
        res = {"ic": {}, "t": {}, "n": 0}
        for h in horizons:
            ics = []
            for d in dates[: -h - 1: step] if len(dates) > h + 1 else []:
                v = _xs_spearman(panel.loc[d], fwd[h].loc[d], min_names)
                if v is not None:
                    ics.append(v)
            t = nw_mean_t(np.array(ics), int(np.ceil(h / step))) if ics else None
            res["ic"][h] = round(float(np.mean(ics)), 3) if ics else None
            res["t"][h] = None if t is None else round(t, 2)
            if h == max(horizons):
                res["n"], res["grade"] = len(ics), factor_grade(ics, t)
        if ref is not None:
            cs = [v for d in dates[::step] for v in [_xs_spearman(panel.loc[d], ref.reindex(index=idx).loc[d], min_names)]
                  if v is not None]
            res["corr_tech"] = round(float(np.mean(cs)), 2) if cs else None
        out[name] = res
    return out


def pit_factor_ic(closes: pd.DataFrame, members: dict[str, list[str]], lookbacks: tuple = (20, 60), h: int = 20,
                  step: int = 5, min_names: int = 30) -> dict[str, dict]:
    """The same check for return factors on the stocks that were actually among the most traded at the
    time (point-in-time top 90 from the archive), entering at the next close (the archive has no opens)."""
    closes = closes.sort_index()
    fwd = closes.shift(-h - 1) / closes.shift(-1) - 1
    out = {}
    for n in lookbacks:
        past = closes / closes.shift(n) - 1
        ics = []
        for d in closes.index[n::step]:
            key = d.strftime("%Y-%m-%d")
            if key not in members or d not in fwd.index:
                continue
            cols = [c for c in members[key][:90] if c in closes.columns]
            v = _xs_spearman(past.loc[d, cols], fwd.loc[d, cols], min_names) if cols else None
            if v is not None:
                ics.append(v)
        t = nw_mean_t(np.array(ics), int(np.ceil(h / step))) if ics else None
        out[f"r{n}"] = {"ic": round(float(np.mean(ics)), 3) if ics else None, "t": None if t is None else round(t, 2),
                        "n": len(ics), "grade": factor_grade(ics, t)}
    return out


# --------------------------------------------------------------------------- #
# The ranking in each market phase (review 2026-11-14d, C5) - shown, never used to change the list
# --------------------------------------------------------------------------- #
REGIME_NAMES = {"bull": "多頭趨勢", "range": "高檔盤整", "pullback": "多頭回檔", "rebound": "空頭反彈", "bear": "空頭趨勢"}


def regime_spread(panel: pd.DataFrame, closes: pd.DataFrame, states: pd.Series, n: int = 12, days: int = 20,
                  min_names: int = 20) -> dict[str, dict]:
    """For every day with a market phase: the rank IC of the score against the next `days` sessions'
    returns (close to close) and the mean return of the top n minus the median. Grouped by phase: the
    number of days, of separate stretches, the mean IC and the mean spread."""
    fwd = closes.shift(-days) / closes - 1
    rows = []
    for d in panel.index.intersection(states.index).intersection(fwd.index):
        s, r = panel.loc[d].dropna(), fwd.loc[d].dropna()
        idx = s.index.intersection(r.index)
        if len(idx) < min_names:
            continue
        top = s[idx].sort_values(ascending=False).index[:n]
        rows.append((d, states.loc[d], float(s[idx].rank().corr(r[idx].rank())), float(r[top].mean() - r[idx].median())))
    out = {}
    for st in REGIME_NAMES:
        sub = [x for x in rows if x[1] == st]
        if not sub:
            continue
        segs, prev = 0, None
        pos = {d: i for i, d in enumerate(states.index)}
        for d, *_ in sub:
            if prev is None or pos[d] - pos[prev] > 1:
                segs += 1
            prev = d
        out[st] = {"name": REGIME_NAMES[st], "days": len(sub), "segments": segs,
                   "ic": round(float(np.mean([x[2] for x in sub])), 3),
                   "spread": round(float(np.mean([x[3] for x in sub])), 4)}
    return out


# --------------------------------------------------------------------------- #
# Industry table for the whole market (review 2026-11-14d, C6) - a description, not a forecast
# --------------------------------------------------------------------------- #
def industry_table(closes: pd.DataFrame, values: pd.DataFrame, industry: dict[str, str], index: pd.Series,
                   names: dict[str, str] | None = None, liq: float = 2e7, min_n: int = 5) -> list[dict]:
    """closes / values: dates x codes (unadjusted close and traded value, the last 61+ sessions).
    Members: 20-day average traded value >= liq. Per industry with >= min_n members: the median 20/60-day
    return minus the index's, the share of the members' traded value over the last 20 sessions and its
    change against the 20 before (percentage points), the number of members whose 60-day return is in the
    top 20% of all members, and the strongest and weakest member over 20 days."""
    closes, values = closes.sort_index(), values.sort_index()
    if len(closes) < 61:
        return []
    avg = values.tail(20).mean()
    mem = [c for c in avg.index if avg[c] >= liq and c in industry and pd.notna(closes[c].iloc[-1])]
    r20 = closes[mem].iloc[-1] / closes[mem].iloc[-21] - 1
    r60 = closes[mem].iloc[-1] / closes[mem].iloc[-61] - 1
    ix = index.dropna()
    i20 = float(ix.iloc[-1] / ix.iloc[-21] - 1) if len(ix) >= 21 else None
    i60 = float(ix.iloc[-1] / ix.iloc[-61] - 1) if len(ix) >= 61 else None
    v_now, v_before = values[mem].tail(20).sum(), values[mem].iloc[-40:-20].sum()
    tot_now, tot_before = float(v_now.sum()), float(v_before.sum())
    strong = set(r60.dropna()[r60.dropna() >= r60.dropna().quantile(0.8)].index)
    names = names or {}
    out = []
    for ind in sorted({industry[c] for c in mem}):
        cs = [c for c in mem if industry[c] == ind]
        if len(cs) < min_n:
            continue
        a20, a60 = r20[cs].dropna(), r60[cs].dropna()
        share = float(v_now[cs].sum()) / tot_now if tot_now else None
        before = float(v_before[cs].sum()) / tot_before if tot_before else None
        best, worst = (a20.idxmax(), a20.idxmin()) if len(a20) else (None, None)
        out.append({"name": ind, "n": len(cs),
                    "rel20": None if i20 is None or a20.empty else round(float(a20.median()) - i20, 4),
                    "rel60": None if i60 is None or a60.empty else round(float(a60.median()) - i60, 4),
                    "share": None if share is None else round(share, 4),
                    "share_chg": None if share is None or before is None else round(share - before, 4),
                    "strong": sum(c in strong for c in cs),
                    "best": None if best is None else [best, names.get(best, best), round(float(a20[best]), 4)],
                    "worst": None if worst is None else [worst, names.get(worst, worst), round(float(a20[worst]), 4)]})
    return sorted(out, key=lambda r: -(r["share"] or 0))


def concentration(industries: list[str], names: list[str], weights: np.ndarray, rets: pd.DataFrame) -> dict:
    """Industry shares of the default weights and the most correlated pair (review 2026-11-14d, C10i).
    The industry cap counts names, but the weights follow the stop distance, so two close stops in one
    industry can carry a large share."""
    share: dict[str, list] = {}
    for ind, w in zip(industries, weights):
        cell = share.setdefault(ind, [0.0, 0])
        cell[0] += float(w)
        cell[1] += 1
    inds = sorted(([k, round(v[0], 4), v[1]] for k, v in share.items()), key=lambda x: -x[1])
    c = rets.tail(60).corr()
    best = None
    cols = list(rets.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            v = c.iloc[i, j]
            if pd.notna(v) and (best is None or v > best[2]):
                best = [names[i], names[j], round(float(v), 2)]
    return {"industries": inds, "top_pair": best}
