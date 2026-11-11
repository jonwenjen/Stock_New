"""Leading indicators: derivatives positioning, cross-asset spreads, breadth, revenue momentum.

Fetchers are thin; everything that computes is a pure function so it can be tested
without the network. Thresholds quoted in the readings are common market rules of
thumb, shown as context - none of them has been validated on this site's own data.
"""
from __future__ import annotations

import re

import pandas as pd

from . import http
from .twse import _rwd, num

TAIFEX = "https://openapi.taifex.com.tw/v1"
http.HOST_INTERVAL.setdefault("mopsov.twse.com.tw", 5.0)
http.HOST_INTERVAL.setdefault("openapi.taifex.com.tw", 0.5)
ADR_SHARES = 5  # one TSM ADR = five common shares


# ----------------------------- futures positioning ----------------------------- #
def retail_position(inst_rows: list[dict], fut_rows: list[dict], name: str, code: str) -> dict | None:
    """Retail (non-institutional) net position in an index future, as a share of open interest.

    Every contract has a long and a short, so whatever the three institutional groups
    hold net, everyone else holds the opposite. ratio > 0 means retail is net long.
    """
    inst = [r for r in inst_rows if r.get("ContractCode") == name]
    total = sum(num(r.get("OpenInterest")) or 0 for r in fut_rows
                if r.get("Contract") == code and r.get("TradingSession") == "一般")
    if not inst or not total:
        return None
    net = -sum(num(r.get("OpenInterest(Net)")) or 0 for r in inst)
    return {"net": net, "oi": total, "ratio": net / total}


def futures_positions() -> dict:
    inst = http.get_json(f"{TAIFEX}/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate")
    fut = http.get_json(f"{TAIFEX}/DailyMarketReportFut")
    d = inst[0]["Date"]
    return {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
            "mtx": retail_position(inst, fut, "小型臺指期貨", "MTX"),
            "tmf": retail_position(inst, fut, "微型臺指期貨", "TMF")}


def put_call() -> list[dict]:
    """TAIEX options put/call ratios for the last ~20 sessions, oldest first (ratio 1.0 = 100%)."""
    rows = http.get_json(f"{TAIFEX}/PutCallRatio")
    out = [{"date": f"{r['Date'][:4]}-{r['Date'][4:6]}-{r['Date'][6:]}",
            "pcr_oi": (num(r.get("PutCallOIRatio%")) or 0) / 100,
            "pcr_vol": (num(r.get("PutCallVolumeRatio%")) or 0) / 100} for r in rows]
    return sorted(out, key=lambda r: r["date"])


# ----------------------------- securities lending shorts ----------------------------- #
def sbl_table(date: str) -> dict[str, dict]:
    """Per-stock securities-lending short balance and margin short balance (shares)."""
    j = _rwd("marginTrading/TWT93U", date=date.replace("-", ""))
    return {r[0].strip(): {"name": r[1].strip(), "short": num(r[6]) or 0, "sbl": num(r[12]) or 0,
                           "sbl_sold": num(r[9]) or 0} for r in j["data"]}


def sbl_moves(now: dict, before: dict, closes: dict, min_value: float = 5.0) -> list[dict]:
    """Stocks where institutional (lending) shorts grew most over the window.

    value in 100M TWD; chg is the change in lending-short balance, short_chg the
    change in retail margin shorts over the same days.
    """
    out = []
    for code, n in now.items():
        b, close = before.get(code), closes.get(code)
        if not b or not close or not b["sbl"] or not re.fullmatch(r"[1-9]\d{3}", code):
            continue
        value = n["sbl"] * close / 1e8
        if value < min_value:
            continue
        out.append({"code": code, "name": n["name"], "value": value, "lots": n["sbl"] / 1000,
                    "chg": n["sbl"] / b["sbl"] - 1,
                    "short_chg": (n["short"] / b["short"] - 1) if b["short"] else None,
                    "sbl_vs_short": n["sbl"] / n["short"] if n["short"] else None})
    return sorted(out, key=lambda r: -r["chg"])


# ----------------------------- cross-asset ----------------------------- #
def adr_premium(adr: pd.Series, local: pd.Series, usdtwd: pd.Series) -> pd.Series:
    """TSM ADR price over the Taiwan share price it represents, minus one.

    Uses days on which both markets traded; the exchange rate is carried forward
    over its own gaps (the published series lags by a few days).
    """
    df = pd.concat([adr.rename("adr"), local.rename("tw"), usdtwd.rename("fx")], axis=1).sort_index()
    df["fx"] = df["fx"].ffill()
    df = df.dropna()
    return (df["adr"] * df["fx"] / (ADR_SHARES * df["tw"]) - 1).rename("premium")


def percentile(series: pd.Series, n: int = 250) -> float | None:
    """Where the latest value sits within its own last n observations, 0..1."""
    s = series.dropna().tail(n)
    return float((s <= s.iloc[-1]).mean()) if len(s) >= 20 else None


def breadth(frames: dict[str, pd.DataFrame], n: int = 120) -> pd.DataFrame:
    """Share of stocks closing above their 20- and 60-day averages, per day (tech_frame inputs)."""
    close = pd.DataFrame({s: f["close"] for s, f in frames.items()})
    out = pd.DataFrame(index=close.index)
    for k in (20, 60):
        ma = pd.DataFrame({s: f[f"ma{k}"] for s, f in frames.items()})
        valid = ma.notna() & close.notna()
        out[f"above{k}"] = ((close > ma) & valid).sum(axis=1) / valid.sum(axis=1).where(valid.sum(axis=1) >= 10)
    return out.dropna().tail(n)


def term_structure(vix: pd.Series, vix3m: pd.Series) -> pd.Series:
    """VIX / VIX3M. Above 1 means near-term fear is priced higher than three months out."""
    df = pd.concat([vix.rename("a"), vix3m.rename("b")], axis=1).dropna()
    return (df["a"] / df["b"]).rename("ratio")


# ----------------------------- monthly revenue momentum ----------------------------- #
def parse_mops_revenue(html: str) -> dict[str, float]:
    """Monthly revenue table from the MOPS archive -> {company code: revenue in thousand TWD}."""
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", "").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        if len(cells) >= 3 and re.fullmatch(r"\d{4}", cells[0]):
            v = num(cells[2])
            if v is not None:
                out[cells[0]] = v
    return out


def mops_revenue(ym: str) -> dict[str, float]:
    """Listed companies' revenue for month 'YYYY-MM' from the MOPS static archive."""
    y, m = int(ym[:4]) - 1911, int(ym[5:])
    r = http.request("GET", f"https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{y}_{m}.html", retries=1, timeout=60)
    table = parse_mops_revenue(r.content.decode("big5", "ignore"))
    if len(table) < 200:
        raise RuntimeError(f"revenue table for {ym} looks incomplete ({len(table)} rows)")
    return table


def revenue_momentum(months: dict[str, float]) -> dict | None:
    """3-month vs 12-month average revenue for one company. months: {'YYYY-MM': revenue}.

    ratio > 1 means recent revenue runs above its yearly average; cross_up marks the
    month the short average moves above the long one - an early sign of a turn,
    visible before the quarterly report.
    """
    keys = sorted(months)
    vals = [months[k] for k in keys]
    if len(vals) < 12 or min(vals[-12:]) <= 0:
        return None
    ratio = (sum(vals[-3:]) / 3) / (sum(vals[-12:]) / 12)
    out = {"month": keys[-1], "ratio": ratio, "cross_up": False, "cross_down": False, "rising": None}
    if len(vals) >= 13 and min(vals[-13:-1]) > 0:
        prev = (sum(vals[-4:-1]) / 3) / (sum(vals[-13:-1]) / 12)
        out.update({"prev": prev, "rising": ratio > prev,
                    "cross_up": ratio > 1 >= prev, "cross_down": ratio < 1 <= prev})
    return out


# ----------------------------- tracking past picks ----------------------------- #
def cohort_results(cohorts: list[dict], frames: dict[str, pd.DataFrame], bench: dict[str, str]) -> list[dict]:
    """How each past week's picks have done since, against their market's index.

    cohorts: [{"date": 'YYYY-MM-DD', "picks": [{"symbol", "market", "close"}]}].
    bench: market -> benchmark symbol in frames. Returns are simple, from the pick
    date's close to the latest close, equal-weighted per market.
    """
    out = []
    for c in cohorts:
        d0 = pd.Timestamp(c["date"])
        row = {"date": c["date"], "n": len(c["picks"])}
        for mk in sorted({p["market"] for p in c["picks"]}):
            rets = []
            for p in c["picks"]:
                f = frames.get(p["symbol"])
                if p["market"] == mk and f is not None and p.get("close") and f.index[-1] > d0:
                    rets.append(float(f["close"].iloc[-1]) / p["close"] - 1)
            b = frames.get(bench.get(mk, ""))
            if not rets or b is None:
                continue
            base = b["close"][b.index <= d0]
            if base.empty or b.index[-1] <= d0:
                continue
            bret = float(b["close"].iloc[-1] / base.iloc[-1] - 1)
            days = int((b.index > d0).sum())
            row[mk] = {"n": len(rets), "ret": sum(rets) / len(rets), "bench": bret,
                       "excess": sum(rets) / len(rets) - bret, "days": days,
                       "win": sum(r > bret for r in rets) / len(rets)}
        if len(row) > 2:
            out.append(row)
    return out


# ----------------------------- podcast attention vs positioning ----------------------------- #
def attention_signal(mentions: int, weeks: int, f5: float | None, margin_chg5: float | None) -> str | None:
    """Label a talked-about Taiwan stock by who is actually buying it.

    f5: foreign net buying over five days in average-daily-volumes; margin_chg5:
    change in margin balance. Early = institutions accumulate while retail leverage
    is quiet and the talk is new. Crowded = talk has persisted, retail leverage is
    surging and institutions are selling.
    """
    if not mentions or f5 is None:
        return None
    m = margin_chg5 or 0.0
    if weeks <= 1 and f5 >= 0.15 and m <= 0.02:
        return "法人先行：話題剛起，法人買、融資未跟"
    if (weeks >= 2 or mentions >= 3) and f5 <= -0.15 and m >= 0.05:
        return "過熱警訊：話題延燒，融資追、法人賣"
    if f5 <= -0.15 and m >= 0.05:
        return "籌碼轉弱：融資增、法人賣"
    return None
