"""Mutual funds: NAV history, peer ranking and benchmark figures, plus our own evaluation.

Data comes from Anue (鉅亨網) fund pages' public JSON, which carries Morningstar
category rankings and benchmark returns. Returns shown on the site are the
provider's (they include distributions); risk figures are computed here from the
NAV series, which does not add distributions back - so for a distributing share
class volatility is fine but drawdown is overstated. The config therefore prefers
accumulating classes.
"""
from __future__ import annotations

import datetime as dt
import math
from urllib.parse import quote

import numpy as np
import pandas as pd

from . import http

http.HOST_INTERVAL.setdefault("fund.api.cnyes.com", 1.0)
API = "https://fund.api.cnyes.com/fund/api/v1/funds"
PERIODS = {"1m": "return1Month", "3m": "return3Month", "6m": "return6Month", "ytd": "returnYTD",
           "1y": "return1Year", "3y": "accReturn3Year", "5y": "accReturn5Year", "10y": "accReturn10Year"}
WEIGHTS = {"peer": 0.40, "excess": 0.20, "sharpe": 0.25, "drawdown": 0.15}
GRADES = ((75, "優異"), (60, "良好"), (45, "普通"), (30, "偏弱"), (-1, "落後"))


# ----------------------------- fetch ----------------------------- #
def fetch_nav(fund_id: str) -> dict:
    """Fund profile + full NAV history in one request."""
    return http.get_json(f"{API}/{quote(fund_id, safe='')}/nav", params={"format": "chart"}, retries=2)["items"]


def fetch_performance(fund_id: str) -> dict:
    """Returns vs benchmark index, category averages and category rankings."""
    return http.get_json(f"{API}/{quote(fund_id, safe='')}/performance", retries=2)["items"]


# ----------------------------- pure helpers ----------------------------- #
def frac(x) -> float | None:
    """Provider percentages (12.3 = 12.3%) -> fractions; junk and placeholders -> None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v / 100 if math.isfinite(v) and abs(v) < 99999 else None


def nav_series(items: dict) -> pd.Series:
    """Ascending daily NAV series from the provider's parallel arrays."""
    s = pd.Series(items.get("nav") or [], index=pd.to_datetime(items.get("tradeDate") or [], unit="s"), dtype=float)
    s = s[s > 0]
    return s[~s.index.duplicated(keep="first")].sort_index()


def annualise(cumulative: float | None, years: float) -> float | None:
    if cumulative is None or cumulative <= -1:
        return None
    return (1 + cumulative) ** (1 / years) - 1


def risk_metrics(nav: pd.Series, ann_return: float | None, rf: float) -> dict:
    """Volatility, drawdown, Sharpe and monthly win rate over the last three years of NAV.

    ann_return: the annualised total return to use for Sharpe (provider figure when
    available, so distributions count); falls back to the NAV's own return.
    """
    if len(nav) < 60:
        return {}
    end = nav.index[-1]
    w3 = nav[nav.index >= end - pd.DateOffset(years=3)]
    w1 = nav[nav.index >= end - pd.DateOffset(years=1)]
    span = (w3.index[-1] - w3.index[0]).days / 365.25

    def vol(w: pd.Series) -> float | None:
        r = np.log(w / w.shift(1)).dropna()
        if len(r) < 30:
            return None
        per_year = len(r) / max((w.index[-1] - w.index[0]).days / 365.25, 1e-9)
        return float(r.std() * math.sqrt(per_year))

    def mdd(w: pd.Series) -> float:
        return float((w / w.cummax() - 1).min())

    out = {"vol_1y": vol(w1), "vol_3y": vol(w3), "mdd_1y": mdd(w1), "mdd_3y": mdd(w3), "span_years": round(span, 2)}
    if ann_return is None and span >= 0.9:
        ann_return = (w3.iloc[-1] / w3.iloc[0]) ** (1 / span) - 1
    if ann_return is not None and out["vol_3y"]:
        out["sharpe"] = (ann_return - rf) / out["vol_3y"]
    monthly = w3.resample("ME").last().pct_change().dropna()
    if len(monthly) >= 6:
        out["win_months"] = float((monthly > 0).mean())
    return out


def _scale(x: float | None, lo: float, hi: float) -> float | None:
    """Map lo..hi linearly onto 0..100, clipped."""
    if x is None or not math.isfinite(x):
        return None
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def performance_score(beat: dict, excess_ann: float | None, sharpe: float | None,
                      mdd_3y: float | None) -> tuple[float | None, str | None, dict]:
    """0-100 score from four views of the same question: was holding this fund worth it?

    beat: share of category peers beaten per period, 0-100 (keys '1y', '3y', '5y').
    excess_ann: annualised return minus the benchmark index's, as a fraction.
    """
    pw = {"1y": 0.30, "3y": 0.45, "5y": 0.25}
    have = {k: v for k, v in beat.items() if k in pw and v is not None}
    peer = sum(pw[k] * v for k, v in have.items()) / sum(pw[k] for k in have) if have else None
    parts = {
        "peer": peer,
        "excess": _scale(excess_ann, -0.10, 0.10),     # -10pp a year -> 0, +10pp -> 100
        "sharpe": _scale(sharpe, -0.75, 1.75),         # 0.5 is the midpoint
        "drawdown": _scale(mdd_3y, -0.50, 0.0),        # -50% -> 0, no drawdown -> 100
    }
    got = {k: v for k, v in parts.items() if v is not None}
    if len(got) < 2:  # one lonely number is not an evaluation
        return None, None, parts
    score = sum(WEIGHTS[k] * v for k, v in got.items()) / sum(WEIGHTS[k] for k in got)
    grade = next(label for floor, label in GRADES if score >= floor)
    return round(score, 1), grade, {k: (None if v is None else round(v, 1)) for k, v in parts.items()}


def verdict(r: dict) -> str:
    """Plain-language reading of the evaluation, built only from the record's own numbers."""
    bits = []
    beat = r.get("beat") or {}
    period = next((p for p in ("3y", "1y", "5y") if beat.get(p) is not None), None)
    if period:
        b = beat[period]
        name = {"1y": "近一年", "3y": "近三年", "5y": "近五年"}[period]
        bits.append(f"{name}報酬贏過 {b:.0f}% 的同類基金" + ("，屬前段班" if b >= 75 else "，落在後段" if b <= 25 else ""))
    ex = r.get("excess_ann")
    if ex is not None and abs(ex) >= 0.005:
        bits.append(f"年化報酬{'高於' if ex > 0 else '低於'}對比指數 {abs(ex) * 100:.1f} 個百分點")
    sh = r.get("sharpe")
    if sh is not None:
        bits.append(f"每承擔一分風險換得的報酬{'高' if sh >= 1 else '尚可' if sh >= 0.4 else '偏低'}（Sharpe {sh:.2f}）")
    dd = r.get("mdd_3y")
    if dd is not None:
        bits.append(f"三年內最大回檔 {dd * 100:.0f}%")
    if not bits:
        return "成立時間或資料不足，暫無法評估。"
    tail = ""
    if r.get("dist") and r["dist"] not in ("", "None", "無"):
        tail = "（配息級別，回檔數字未還原配息，會偏大）"
    return "；".join(bits) + "。" + tail


def build_record(fund_id: str, group: str, items: dict, perf: dict, rf_by_currency: dict) -> tuple[dict, pd.Series]:
    """Combine provider figures with our own metrics into one record (+ the NAV series for charts)."""
    nav = nav_series(items)
    currency = items.get("classCurrencyLocal") or ""
    rf = (rf_by_currency.get(currency, rf_by_currency.get("default", 2.0))) / 100
    ret = {k: frac((perf.get("returns") or {}).get(f) if (perf.get("returns") or {}).get(f) is not None
                   else items.get(f)) for k, f in PERIODS.items()}
    bench = {k: frac((perf.get("indexReturns") or {}).get(f)) for k, f in PERIODS.items()}
    cat = {k: frac((perf.get("categoryAverages") or {}).get(f)) for k, f in PERIODS.items()}
    ranks = perf.get("categoryRankings") or {}
    rank = {k: {"rank": ranks[f].get("ranking"), "total": ranks[f].get("total"), "beat": ranks[f].get("surpassRate")}
            for k, f in PERIODS.items() if isinstance(ranks.get(f), dict) and ranks[f].get("total")}
    beat = {k: v["beat"] for k, v in rank.items()}

    ann3, bann3 = annualise(ret["3y"], 3), annualise(bench["3y"], 3)
    if ann3 is not None and bann3 is not None:
        excess, excess_basis = ann3 - bann3, "3y"
    elif ret["1y"] is not None and bench["1y"] is not None:
        excess, excess_basis = ret["1y"] - bench["1y"], "1y"
    else:
        excess, excess_basis = None, None
    risk = risk_metrics(nav, ann3 if ann3 is not None else ret["1y"], rf)
    score, grade, parts = performance_score(beat, excess, risk.get("sharpe"), risk.get("mdd_3y"))
    if risk.get("span_years", 0) < 1:  # too young to judge
        score, grade = None, None

    ts = lambda k: dt.datetime.fromtimestamp(items[k], dt.timezone.utc).date().isoformat() if items.get(k) else None  # noqa: E731
    asset_date = ts("netAssetDate")
    rec = {
        "symbol": f"F-{fund_id}", "code": fund_id, "kind": "fund", "market": "FUND", "group": group,
        "name": items.get("displayNameLocal") or fund_id,
        "industry": items.get("categoryLocal") or items.get("globalCategory") or "",
        "currency": currency, "company": items.get("investmentProviderShortName") or items.get("fundCompanyNameLocal"),
        "manager": items.get("managerNameLocal"), "rr": items.get("riskLevel"), "stars": items.get("starRating"),
        "fee": items.get("managementFee"), "dist": items.get("dividendDistributionFrequency") or "",
        "inception": ts("inceptionDate"), "area": items.get("investmentArea"),
        "bench_name": items.get("primaryProspectusIndexName") or items.get("categoryIndexNameLocal"),
        "aum": items.get("totalNetAsset") if asset_date and asset_date >= "2025-01-01" else None,
        "aum_currency": items.get("netAssetCurrency"),
        "close": float(nav.iloc[-1]) if len(nav) else None,
        "date": nav.index[-1].strftime("%Y-%m-%d") if len(nav) else None,
        "chg1d": float(nav.iloc[-1] / nav.iloc[-2] - 1) if len(nav) > 1 else None,
        "ret": ret, "bench": bench, "cat": cat, "rank": rank, "beat": beat,
        "ret_1y_twd": frac(items.get("return1YearLocal")),
        "excess_ann": excess, "excess_basis": excess_basis,
        **{k: risk.get(k) for k in ("vol_1y", "vol_3y", "mdd_1y", "mdd_3y", "sharpe", "win_months", "span_years")},
        "score": score, "grade": grade, "parts": parts,
    }
    rec["verdict"] = verdict(rec)
    return rec, nav
