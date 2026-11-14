"""Multi-factor scoring and horizon forecasts.

Every factor is squashed into [-1, +1]; horizon scores are weighted blends.
All functions here are pure (no network) so they can be unit-tested and
back-tested with the exact same code that produces the live numbers.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import indicators as ind

HORIZONS = {"1w": 5, "1m": 21, "1q": 63}  # trading days

# Weight of each factor group per horizon (re-normalised when a group is missing).
# Podcast attention ("buzz") carries no weight: being talked about says nothing reliable about
# direction, and heavy attention is at least as often a sign of a crowded trade. It is shown, not scored.
# One direction score for every horizon. A 2026-11 review measured the cross-sectional IC of
# each block on this site's own history: the short-term block (5-day return, RSI, MACD) had
# none, even for one-week returns, and the slow technical score did best at every horizon.
# The fundamental and chip groups have no history to test, so they carry less weight until
# they do. The horizons still differ - in how wide the range is.
_TW = {"tech": 0.60, "chips": 0.25, "fund": 0.15, "buzz": 0.00}
WEIGHTS = {"1w": _TW, "1m": _TW, "1q": _TW}
# US "chips" are congressional filings (up to 45 days late) and quarterly 13F holdings:
# real information, but too slow to drive a one-week call, so they weigh less up front.
_US = {"tech": 0.80, "chips": 0.05, "fund": 0.15, "buzz": 0.00}
WEIGHTS_SLOW_CHIPS = {"1w": _US, "1m": _US, "1q": _US}
OVERALL = {"1w": 0.3, "1m": 0.4, "1q": 0.3}


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _num(x) -> float | None:
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# --------------------------------------------------------------------------- #
# Technical factors (vectorised: one row per trading day)
# --------------------------------------------------------------------------- #
def tech_frame(df: pd.DataFrame) -> pd.DataFrame:
    """df needs columns open/high/low/close/volume. Returns indicator + score columns."""
    c = df["close"].astype(float)
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    for n in (5, 20, 60, 120, 240):
        out[f"ma{n}"] = ind.sma(c, n)
    out["rsi"] = ind.rsi(c, 14)
    _, _, hist = ind.macd(c)
    out["macd_hist"] = hist
    out["atr"] = ind.atr(df["high"].astype(float), df["low"].astype(float), c, 14)
    out["vol"] = ind.daily_vol(c, 60)
    out["pos250"] = ind.range_position(c, 250)
    # distance from the 52-week high (1 = at the high): the one price factor that held up on
    # Taiwan data after removing market beta (George & Hwang 2004: anchoring on the old high)
    out["hi52"] = c / df["high"].astype(float).rolling(252, min_periods=200).max()
    for n in (5, 20, 60, 120):
        out[f"r{n}"] = ind.pct_change_n(c, n)

    sgn = lambda a, b: np.sign((a - b).fillna(0))  # noqa: E731
    slope60 = (out["ma60"] / out["ma60"].shift(20) - 1) / (out["vol"] * math.sqrt(20))
    trend = (
        0.20 * sgn(c, out["ma20"])
        + 0.30 * sgn(c, out["ma60"])
        + 0.20 * sgn(c, out["ma120"])
        + 0.15 * sgn(out["ma20"], out["ma60"])
        + 0.15 * np.tanh(slope60.fillna(0) * 2)
    )

    def zret(n: int) -> pd.Series:  # volatility-normalised return
        return np.tanh((out[f"r{n}"] / (out["vol"] * math.sqrt(n))).fillna(0))

    rsi_s = pd.Series(0.0, index=df.index)
    r = out["rsi"]
    rsi_s = rsi_s.mask(r >= 80, -0.30).mask((r >= 70) & (r < 80), -0.10)
    rsi_s = rsi_s.mask((r >= 50) & (r < 70), 0.15).mask((r > 30) & (r < 50), -0.15)
    rsi_s = rsi_s.mask(r <= 30, 0.20)
    macd_s = 0.2 * np.sign(hist.fillna(0)) + 0.1 * np.sign(hist.diff().fillna(0))
    mom_s = (0.4 * zret(5) + macd_s + rsi_s).clip(-1, 1)
    mom_m = (0.6 * zret(60) + 0.4 * zret(20)).clip(-1, 1)
    mom_l = (0.6 * zret(120) + 0.8 * (out["pos250"].fillna(0.5) - 0.5)).clip(-1, 1)

    out["trend"] = trend.clip(-1, 1)
    out["mom_s"], out["mom_m"], out["mom_l"] = mom_s, mom_m, mom_l
    out["t_slow"] = (0.4 * out["trend"] + 0.35 * mom_l + 0.25 * mom_m).clip(-1, 1)
    # The per-horizon columns are kept for the code that loops over horizons; they are one score.
    out["t_1w"] = out["t_1m"] = out["t_1q"] = out["t_slow"]
    return out


def tech_reasons(row: pd.Series) -> list[tuple[str, float]]:
    """Human-readable (text, signed weight) notes for the latest bar."""
    notes: list[tuple[str, float]] = []
    c = row["close"]
    ma20, ma60, ma120 = _num(row.get("ma20")), _num(row.get("ma60")), _num(row.get("ma120"))
    if ma20 and ma60 and ma120:
        if c > ma20 > ma60 > ma120:
            notes.append(("均線多頭排列（價>月線>季線>半年線）", 0.5))
        elif c < ma20 < ma60 < ma120:
            notes.append(("均線空頭排列（價<月線<季線<半年線）", -0.5))
        elif c > ma60:
            notes.append(("股價站上季線", 0.25))
        else:
            notes.append(("股價跌破季線", -0.25))
    rsi = _num(row.get("rsi"))
    if rsi is not None:
        if rsi >= 80:
            notes.append((f"RSI {rsi:.0f} 嚴重超買，短線追高風險大", -0.3))
        elif rsi >= 70:
            notes.append((f"RSI {rsi:.0f} 進入超買區", -0.1))
        elif rsi <= 30:
            notes.append((f"RSI {rsi:.0f} 超賣，留意反彈", 0.2))
    h = _num(row.get("macd_hist"))
    if h is not None:
        notes.append(("MACD 柱狀體為正，動能偏多", 0.2) if h > 0 else ("MACD 柱狀體為負，動能偏空", -0.2))
    r20, r60 = _num(row.get("r20")), _num(row.get("r60"))
    if r60 is not None and abs(r60) >= 0.15:
        notes.append((f"近一季漲跌 {r60:+.0%}", 0.3 if r60 > 0 else -0.3))
    elif r20 is not None and abs(r20) >= 0.08:
        notes.append((f"近一月漲跌 {r20:+.0%}", 0.2 if r20 > 0 else -0.2))
    pos = _num(row.get("pos250"))
    if pos is not None:
        if pos >= 0.95:
            notes.append(("股價位於一年高點附近", 0.15))
        elif pos <= 0.05:
            notes.append(("股價位於一年低點附近", -0.15))
    return notes


# --------------------------------------------------------------------------- #
# Chips (flows / positioning)
# --------------------------------------------------------------------------- #
def chips_tw(m: dict) -> tuple[float | None, list[tuple[str, float]]]:
    """m keys: f5, f20, t5 (net shares / avg daily volume), streak (signed days),
    margin_chg5 (fraction), r5 (price return)."""
    notes: list[tuple[str, float]] = []
    if _num(m.get("f5")) is None:
        return None, notes
    f5, f20, t5 = m["f5"], _num(m.get("f20")) or 0.0, _num(m.get("t5")) or 0.0
    s = 0.35 * math.tanh(f5 / 0.5) + 0.20 * math.tanh(f20 / 1.5) + 0.20 * math.tanh(t5 / 0.25)
    if abs(f5) >= 0.15:
        notes.append((f"外資近5日{'買超' if f5 > 0 else '賣超'}（約 {abs(f5):.1f} 日均量）", 0.35 * math.tanh(f5 / 0.5)))
    if abs(t5) >= 0.08:
        notes.append((f"投信近5日{'買超' if t5 > 0 else '賣超'}", 0.2 * math.tanh(t5 / 0.25)))
    streak = int(m.get("streak") or 0)
    if abs(streak) >= 3:
        w = (0.15 if abs(streak) >= 5 else 0.10) * (1 if streak > 0 else -1)
        s += w
        notes.append((f"外資連續{abs(streak)}日{'買超' if streak > 0 else '賣超'}", w))
    mc, r5 = _num(m.get("margin_chg5")), _num(m.get("r5")) or 0.0
    if mc is not None:
        if mc >= 0.05 and r5 < -0.02:
            s -= 0.15
            notes.append((f"融資增 {mc:+.1%} 但股價下跌，散戶接刀籌碼凌亂", -0.15))
        elif mc <= -0.03 and r5 > 0.01:
            s += 0.10
            notes.append((f"融資減 {mc:+.1%} 而股價上漲，籌碼沉澱", 0.10))
        elif mc >= 0.10:
            s -= 0.05
            notes.append((f"融資一週暴增 {mc:+.1%}，短線過熱", -0.05))
    return clamp(s), notes


def chips_us(m: dict) -> tuple[float | None, list[tuple[str, float]]]:
    """m keys: congress_net (buys - sells, 90d), guru_net (adds - cuts among tracked 13F)."""
    notes: list[tuple[str, float]] = []
    cn, gn = _num(m.get("congress_net")), _num(m.get("guru_net"))
    if cn is None and gn is None:
        return None, notes
    s = 0.0
    if cn:
        w = 0.5 * math.tanh(cn / 3)
        s += w
        notes.append((f"美國國會議員近90日淨{'買進' if cn > 0 else '賣出'} {abs(int(cn))} 筆", w))
    if gn:
        w = 0.5 * math.tanh(gn / 3)
        s += w
        notes.append((f"追蹤的名人基金上季淨{'加碼' if gn > 0 else '減碼'}（{int(gn):+d} 家）", w))
    return clamp(s), notes


# --------------------------------------------------------------------------- #
# Fundamentals
# --------------------------------------------------------------------------- #
def _pe_score(pe: float | None, bands: tuple[float, float, float, float]) -> tuple[float, str | None]:
    a, b, c, d = bands
    if pe is None:
        return 0.0, None
    if pe <= 0:
        return -0.10, "近四季虧損（本益比為負）"
    if pe < a:
        return 0.20, f"本益比 {pe:.1f} 倍，評價偏低"
    if pe < b:
        return 0.10, None
    if pe < c:
        return 0.0, None
    if pe < d:
        return -0.10, f"本益比 {pe:.1f} 倍，評價偏高"
    return -0.20, f"本益比 {pe:.1f} 倍，評價昂貴"


def odd_yield(dy: float | None, pe: float | None) -> bool:
    """A yield above 15%, or one implying a payout above 150% of earnings, is a one-off or an error."""
    return dy is not None and (dy > 15 or (pe is not None and pe > 0 and dy * pe / 100 > 1.5))


def fund_tw(m: dict) -> tuple[float | None, list[tuple[str, float]]]:
    """m keys: rev_yoy, rev_mom, rev_cum_yoy (percent), pe, pb, yield (percent), eps."""
    notes: list[tuple[str, float]] = []
    yoy, pe = _num(m.get("rev_yoy")), _num(m.get("pe"))
    if yoy is None and pe is None and _num(m.get("pb")) is None:
        return None, notes
    s = 0.0
    if yoy is not None:
        w = 0.35 * math.tanh(yoy / 30)
        s += w
        if abs(yoy) >= 10:
            notes.append((f"月營收年增 {yoy:+.1f}%", w))
    cum = _num(m.get("rev_cum_yoy"))
    if cum is not None:
        s += 0.15 * math.tanh(cum / 25)
        if abs(cum) >= 15:
            notes.append((f"累計營收年增 {cum:+.1f}%", 0.15 * math.tanh(cum / 25)))
    mom = _num(m.get("rev_mom"))
    if mom is not None:
        s += 0.10 * math.tanh(mom / 20)
    eps = _num(m.get("eps"))
    # TWSE/TPEx leave the P/E blank for loss-makers: a blank P/E with a loss is a loss, not "neutral".
    w, txt = _pe_score(-1.0 if pe is None and eps is not None and eps < 0 else pe, (12, 20, 30, 45))
    if pe is None and w < 0:
        txt = f"今年累計 EPS {eps:.2f} 元為虧損"
    s += w
    if txt:
        notes.append((txt, w))
    dy = _num(m.get("yield"))
    if odd_yield(dy, pe):
        dy = None  # a one-off payout or a data error: not a dividend you can count on
    if dy is not None and dy >= 3:
        w = 0.10 if dy >= 5 else 0.05
        s += w
        notes.append((f"殖利率 {dy:.1f}%", w))
    pb = _num(m.get("pb"))
    if pb is not None and 0 < pb < 1:
        s += 0.05
        notes.append((f"股價淨值比 {pb:.2f} 低於 1", 0.05))
    if eps is not None and eps > 0:
        s += 0.05
    elif eps is not None and eps < 0 and pe is not None:  # profitable over four quarters, losing this year
        s -= 0.10
        notes.append((f"今年累計 EPS {eps:.2f} 元為虧損", -0.10))
    # Revenue momentum: 3-month average against the 12-month average (monthly filings lead quarterly reports).
    ratio = _num(m.get("rev_ratio"))
    if ratio is not None:
        if m.get("rev_cross_up"):
            s += 0.10
            notes.append(("近 3 月平均營收剛站上 12 月均線，營運轉強", 0.10))
        elif m.get("rev_cross_down"):
            s -= 0.10
            notes.append(("近 3 月平均營收跌破 12 月均線，營運轉弱", -0.10))
        elif ratio >= 1.10:
            s += 0.05
            notes.append((f"近 3 月營收高於年均 {ratio - 1:.0%}", 0.05))
        elif ratio <= 0.90:
            s -= 0.05
            notes.append((f"近 3 月營收低於年均 {1 - ratio:.0%}", -0.05))
    return clamp(s), notes


def fund_us(m: dict) -> tuple[float | None, list[tuple[str, float]]]:
    """m keys: eps_yoy, rev_yoy (percent), pe."""
    notes: list[tuple[str, float]] = []
    g, rv, pe = _num(m.get("eps_yoy")), _num(m.get("rev_yoy")), _num(m.get("pe"))
    if g is None and rv is None and pe is None:
        return None, notes
    s = 0.0
    if g is not None:
        w = 0.40 * math.tanh(g / 40)
        s += w
        if abs(g) >= 10:
            notes.append((f"最新一季 EPS 年增 {g:+.0f}%", w))
    if rv is not None:
        w = 0.35 * math.tanh(rv / 20)
        s += w
        if abs(rv) >= 8:
            notes.append((f"最新一季營收年增 {rv:+.0f}%", w))
    ttm = _num(m.get("eps_ttm"))
    w, txt = _pe_score(-1.0 if pe is None and ttm is not None and ttm <= 0 else pe, (15, 25, 40, 60))
    s += w
    if txt:
        notes.append((txt, w))
    return clamp(s), notes


def buzz_score(mentions: int) -> float | None:
    """Podcast mentions as an attention gauge (0..0.3). Shown as a factor, weighted zero in every score."""
    if not mentions:
        return 0.0
    return clamp(0.3 * math.tanh(mentions / 3))


# --------------------------------------------------------------------------- #
# Blend + forecast
# --------------------------------------------------------------------------- #
def blend(horizon: str, parts: dict[str, float | None], weights: dict | None = None) -> float:
    w = (weights or WEIGHTS)[horizon]
    tot = sum(w[k] for k, v in parts.items() if v is not None and w.get(k))
    if tot <= 0:
        return 0.0
    return clamp(sum(w[k] * v for k, v in parts.items() if v is not None and w.get(k)) / tot)


def label(score: float) -> str:
    if score >= 0.45:
        return "強勢偏多"
    if score >= 0.15:
        return "偏多"
    if score > -0.15:
        return "中性"
    if score > -0.45:
        return "偏空"
    return "強勢偏空"


def forecast(last: float, dvol: float, score: float, days: int, p_up: float | None = None) -> dict:
    """Outlook for one horizon: the score's label and a one-sigma range from realised volatility.

    The range is centred on today's price - the score ranks stocks against each other and
    has shown no ability to say how far one will move. `p_up` is a base rate supplied by the
    caller (how often comparable cases rose); `p_beat` (chance of beating the market's
    median stock) is filled in after the whole market has been ranked.
    """
    dvol = dvol if dvol and math.isfinite(dvol) else 0.02
    sigma = dvol * math.sqrt(days)
    exp_ret = 0.0
    return {
        "score": round(score, 3),
        "label": label(score),
        "p_up": None if p_up is None else round(p_up, 3),
        "exp_ret": round(exp_ret, 4),
        "low": round(last * (1 + exp_ret - sigma), 2),
        "high": round(last * (1 + exp_ret + sigma), 2),
        "sigma": round(sigma, 4),
    }


def _mix(parts: list[tuple[float, float | None]]) -> float | None:
    """Weighted mean of the parts that exist; None when none does."""
    tot = sum(w for w, v in parts if v is not None)
    return clamp(sum(w * v for w, v in parts if v is not None) / tot) if tot else None


def setup_tag(tactical: float | None, structural: float | None, tactical_prev: float | None = None) -> str | None:
    """Name the combination of the slow (structural) and fast (tactical) reads."""
    if tactical is None or structural is None:
        return None
    if structural >= 0.2 and tactical >= 0.15:
        if tactical_prev is not None and tactical_prev < 0.15:
            return "基本面佳，籌碼剛轉強"
        return "基本面與籌碼同步偏多"
    if structural >= 0.2 and tactical <= -0.15:
        return "基本面佳但籌碼偏弱"
    if structural <= -0.2 and tactical >= 0.15:
        return "籌碼強但基本面弱"
    if structural <= -0.2 and tactical <= -0.15:
        return "基本面與籌碼同步偏空"
    return None


def score_stock(tf: pd.DataFrame, chips: float | None, fund: float | None,
                buzz: float | None, slow_chips: bool = False, tech: float | None = None) -> dict:
    """Combine the latest technical row with the other factor groups.

    slow_chips marks positioning data that arrives weeks late (US filings). Besides the
    horizon forecasts two summary reads are returned: `tactical` uses only inputs that
    update daily (price action, daily flows, attention) and `structural` only those
    that update monthly or quarterly (fundamentals, filed holdings).
    """
    row = tf.iloc[-1]
    last, dvol = float(row["close"]), _num(row["vol"]) or 0.02
    out = {"horizons": {}}
    overall = 0.0
    weights = WEIGHTS_SLOW_CHIPS if slow_chips else WEIGHTS
    t = float(row["t_slow"]) if tech is None else tech
    for h, days in HORIZONS.items():
        s = blend(h, {"tech": t, "chips": chips, "fund": fund, "buzz": buzz}, weights)
        out["horizons"][h] = forecast(last, dvol, s, days)
        overall += OVERALL[h] * s
    out["overall"] = round(overall, 3)
    out["label"] = label(overall)
    out["factors"] = {
        "trend": round(float(row["trend"]), 3),
        "momentum": round(float(row["mom_m"]), 3),
        "chips": None if chips is None else round(chips, 3),
        "fund": None if fund is None else round(fund, 3),
        "buzz": None if buzz is None else round(buzz, 3),
    }
    # Two auxiliary reads, neither of which feeds the score again: daily-updated positioning
    # ("chips", Taiwan only) and filed fundamentals (plus US filed holdings).
    tactical = None if slow_chips else chips
    structural = _mix([(0.70, fund), (0.30, chips if slow_chips else None)])
    out["tactical"] = None if tactical is None else round(tactical, 3)
    out["structural"] = None if structural is None else round(structural, 3)
    return out


RISK_BUDGET = 0.01   # share of the portfolio put at risk on one position
MAX_POSITION = 0.25  # never suggest more than a quarter of the portfolio in one name


def trade_plan(row: pd.Series, f1m: dict, overall: float) -> dict:
    """Reference levels (not orders): support, stop, target + a one-line stance.

    Distances are measured in ATR (14-day average true range), so a volatile stock
    gets a wider stop and therefore a smaller suggested position than a steady one.
    """
    c, atr = float(row["close"]), _num(row.get("atr")) or float(row["close"]) * 0.03
    ma20, ma60, rsi = _num(row.get("ma20")), _num(row.get("ma60")), _num(row.get("rsi")) or 50
    support = ma20 if ma20 and ma20 < c else (ma60 if ma60 and ma60 < c else c - 1.5 * atr)
    stop = min(c - 2.5 * atr, support - 0.5 * atr)
    target = max(f1m["high"], c + 2 * atr)
    if overall >= 0.45:
        stance = "強勢股，偏多操作" + ("；RSI 過熱，等拉回月線附近再分批布局" if rsi >= 72 else "；可沿月線分批布局")
    elif overall >= 0.15:
        stance = "偏多看待，拉回支撐不破可分批承接" + ("；短線過熱勿追高" if rsi >= 72 else "")
    elif overall > -0.15:
        stance = "多空拉鋸，觀望為主，等待方向表態"
    elif overall > -0.45:
        stance = "偏空看待，反彈減碼、不宜攤平"
    else:
        stance = "弱勢股，避開或嚴設停損"
    stop = max(stop, 0.01)
    # reward to the one-month target for each unit of risk down to the stop
    rr = (target - c) / (c - stop) if c > stop else None
    stop_pct = (c - stop) / c if c > stop else None
    # position that loses RISK_BUDGET of the portfolio if the stop is hit
    size = min(MAX_POSITION, RISK_BUDGET / stop_pct) if stop_pct else None
    return {"support": round(support, 2), "stop": round(stop, 2), "target": round(target, 2),
            "rr": None if rr is None else round(rr, 2), "stance": stance,
            "atr": round(atr, 2), "atr_pct": round(atr / c, 4),
            "zone_high": round(min(c, support + atr), 2),
            "stop_pct": None if stop_pct is None else round(stop_pct, 4),
            "size": None if size is None else round(size, 3)}


# --------------------------------------------------------------------------- #
# Cross-sectional ranks: the technical score as published (Taiwan adds the 52-week high)
# --------------------------------------------------------------------------- #
def pct_rank(s: pd.Series) -> pd.Series:
    """Percentile within the cross-section, 0 (worst) .. 1 (best)."""
    return s.rank(pct=True, method="average")


def tech_panel(frames: dict[str, pd.DataFrame], market: str) -> pd.DataFrame:
    """dates x symbols panel of the technical score in [-1, 1] as a cross-sectional rank.

    Taiwan: the mean of the ranks of the slow score and of the 52-week-high ratio. US: the
    rank of the slow score (the 52-week high did not hold up there).
    """
    slow = pd.DataFrame({s: f["t_slow"] for s, f in frames.items()})
    r = slow.rank(axis=1, pct=True)
    if market == "TW":
        hi = pd.DataFrame({s: f["hi52"] for s, f in frames.items()}).rank(axis=1, pct=True)
        r = (r + hi.where(hi.notna(), r)) / 2
    return (2 * r - 1).clip(-1, 1)


# --------------------------------------------------------------------------- #
# Residual momentum: strength that is the stock's own, not the market's
# --------------------------------------------------------------------------- #
def residual_momentum(close: pd.Series, bench: pd.Series, beta_n: int = 120, mom_n: int = 60) -> pd.Series:
    """Momentum of the part of a stock's return its market does not explain.

    Each day's return is split into beta x market return and a residual (beta from the
    last beta_n sessions). The series returned is the residual summed over mom_n sessions
    divided by its own volatility over that window - roughly a z-score, so a stock that
    only rose because everything rose scores near zero.
    """
    df = pd.concat([close.rename("c"), bench.rename("b")], axis=1).sort_index()
    df["b"] = df["b"].ffill()
    r = df.dropna().pct_change().dropna()
    if len(r) < beta_n + mom_n:
        return pd.Series(dtype=float)
    beta = (r["c"].rolling(beta_n).cov(r["b"]) / r["b"].rolling(beta_n).var()).shift(1)
    resid = r["c"] - beta * r["b"]
    z = resid.rolling(mom_n).sum() / (resid.rolling(mom_n).std() * math.sqrt(mom_n))
    return z.replace([np.inf, -np.inf], np.nan).dropna()


def factor_ic(scores: dict[str, pd.Series], closes: dict[str, pd.Series], lookback_days: int = 750,
              step: int = 5) -> dict:
    """Rank correlation between any per-stock score series and forward returns, per horizon."""
    out = {}
    s_df, c_df = pd.DataFrame(scores), pd.DataFrame(closes)
    for h, days in HORIZONS.items():
        fwd = c_df.shift(-days) / c_df - 1
        dates = s_df.index[-lookback_days:-days:step] if len(s_df) > days + 50 else []
        ics = []
        for d in dates:
            s, r = s_df.loc[d].dropna(), fwd.loc[d].dropna() if d in fwd.index else pd.Series(dtype=float)
            idx = s.index.intersection(r.index)
            if len(idx) >= 20:
                ics.append(s[idx].rank().corr(r[idx].rank()))
        out[h] = {"samples": len(ics), "ic": round(float(np.nanmean(ics)), 3)} if len(ics) >= 20 else None
    return out


# --------------------------------------------------------------------------- #
# Walk-forward check of the technical score
# --------------------------------------------------------------------------- #
def backtest(frames: dict[str, pd.DataFrame], lookback_days: int = 750, step: int = 5,
             panel: pd.DataFrame | None = None) -> dict:
    """Cross-sectional check: does the score rank future returns?

    frames: symbol -> tech_frame output (indexed by date). `panel` (dates x symbols), when given,
    is the score the site actually ranks by (Taiwan: the slow score combined with distance to the
    52-week high); otherwise each horizon's own column. Uses only information available at each
    date. Universe = today's constituents (survivorship bias: the result is an upper bound).
    """
    res = {}
    for h, days in HORIZONS.items():
        col = f"t_{h}"
        scores = panel if panel is not None else pd.DataFrame({s: f[col] for s, f in frames.items()})
        closes = pd.DataFrame({s: f["close"] for s, f in frames.items()})
        fwd = closes.shift(-days) / closes - 1
        dates = scores.index[-lookback_days:-days:step] if len(scores) > days + 50 else []
        ics, top, bot, allr = [], [], [], []
        for d in dates:
            s, r = scores.loc[d].dropna(), fwd.loc[d].dropna()
            idx = s.index.intersection(r.index)
            if len(idx) < 20:
                continue
            s, r = s[idx], r[idx]
            ics.append(s.rank().corr(r.rank()))
            k = max(1, len(idx) // 5)
            order = s.sort_values()
            top.append(r[order.index[-k:]].mean())
            bot.append(r[order.index[:k]].mean())
            allr.append(r.mean())
        if not ics:
            res[h] = None
            continue
        top_a, bot_a, all_a = np.array(top), np.array(bot), np.array(allr)
        res[h] = {
            "samples": len(ics),
            "ic": round(float(np.nanmean(ics)), 3),
            "top_ret": round(float(top_a.mean()), 4),
            "bottom_ret": round(float(bot_a.mean()), 4),
            "avg_ret": round(float(all_a.mean()), 4),
            "top_beats_avg": round(float((top_a > all_a).mean()), 3),
            "top_beats_bottom": round(float((top_a > bot_a).mean()), 3),
        }
    return res
