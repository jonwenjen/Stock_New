"""Leading indicators: derivatives positioning, cross-asset spreads, breadth, revenue momentum.

Fetchers are thin; everything that computes is a pure function so it can be tested
without the network. Thresholds quoted in the readings are common market rules of
thumb, shown as context - none of them has been validated on this site's own data.
"""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
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
            "pcr_oi": num(r.get("PutCallOIRatio%")) / 100,
            "pcr_vol": (num(r.get("PutCallVolumeRatio%")) or 0) / 100}
           for r in rows if num(r.get("PutCallOIRatio%"))]  # a blank is a missing day, not 0%
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


def mops_revenue(ym: str, board: str = "sii") -> dict[str, float]:
    """Revenue for month 'YYYY-MM' from the MOPS static archive; board "sii" (listed) or "otc"."""
    y, m = int(ym[:4]) - 1911, int(ym[5:])
    r = http.request("GET", f"https://mopsov.twse.com.tw/nas/t21/{board}/t21sc03_{y}_{m}.html", retries=1, timeout=60)
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
    out = {"month": keys[-1], "ratio": ratio, "cross_up": False, "cross_down": False, "rising": None,
           "yoy3": None, "hi12": None}
    # Shown only, weight 0 (review 2026-11-14d, C2): the three latest months against the same three
    # a year earlier, and whether the latest month is the highest of the last twelve.
    ym = [int(k[:4]) * 12 + int(k[5:7]) for k in keys]
    if ym[-1] - ym[-12] == 11:
        out["hi12"] = vals[-1] >= max(vals[-12:-1])
    if len(vals) >= 15 and ym[-1] - ym[-15] == 14 and min(vals[-15:-12]) > 0:
        out["yoy3"] = sum(vals[-3:]) / sum(vals[-15:-12]) - 1
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
    # Descriptive since review 2026-11-14d: f5 is the foreign investors' flow only, so the label says
    # 外資, not 法人; a stock with no margin data (OTC) gets no label instead of a margin change of 0.
    if not mentions or f5 is None or margin_chg5 is None:
        return None
    m = margin_chg5
    if weeks <= 1 and f5 >= 0.15 and m <= 0.02:
        return "話題第 1 週・外資 5 日買超・融資未增"
    if (weeks >= 2 or mentions >= 3) and f5 <= -0.15 and m >= 0.05:
        return f"話題第 {max(weeks, 1)} 週・融資 5 日增・外資 5 日賣超"
    if f5 <= -0.15 and m >= 0.05:
        return "融資 5 日增・外資 5 日賣超"
    return None


# ----------------------------- index options: open-interest walls ----------------------------- #
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def expiry_date(code: str) -> dt.date | None:
    """TAIFEX contract code -> last trading day: 'YYYYMM' is the third Wednesday,
    'YYYYMMWn' the n-th Wednesday and 'YYYYMMFn' the n-th Friday of that month."""
    m = re.fullmatch(r"(\d{4})(\d{2})(?:([WF])(\d))?", code or "")
    if not m:
        return None
    y, mo, kind, n = int(m[1]), int(m[2]), m[3], int(m[4] or 3)
    try:
        return _nth_weekday(y, mo, 4 if kind == "F" else 2, n)
    except ValueError:
        return None


def max_pain(strikes: dict[float, tuple[float, float]]) -> float | None:
    """Settlement price at which option holders collect the least in total.
    strikes: {strike: (call open interest, put open interest)}."""
    ks = sorted(strikes)
    if not ks:
        return None
    pay = lambda s: sum(c * max(0.0, s - k) + p * max(0.0, k - s) for k, (c, p) in strikes.items())  # noqa: E731
    return min(ks, key=pay)


def option_walls(rows: list[dict], after: dt.date, spot: float, contract: str = "TXO") -> list[dict]:
    """Largest call / put open interest per expiry that is still alive after `after`.

    Returns the nearest Wednesday expiry and the nearest monthly one (a single entry
    when they coincide), each with the call wall (largest call open interest at or
    above the index), the put wall (largest put open interest at or below it), the
    max-pain strike and the strike ladder. Friday weeklies are skipped: too thin.
    """
    book: dict[str, dict[float, list[float]]] = {}
    for r in rows:
        code = r.get("ContractMonth(Week)", "")
        if r.get("Contract") != contract or r.get("TradingSession") != "一般" or "F" in code:
            continue
        k, oi = num(r.get("StrikePrice")), num(r.get("OpenInterest")) or 0.0
        if k is None:
            continue
        cell = book.setdefault(code, {}).setdefault(k, [0.0, 0.0])
        cell[0 if r.get("CallPut") == "買權" else 1] += oi
    alive = sorted((expiry_date(code), code) for code in book if expiry_date(code) and expiry_date(code) > after)
    if not alive:
        return []
    chosen = alive[:1] + ([] if len(alive[0][1]) == 6 else [x for x in alive[1:] if len(x[1]) == 6][:1])
    out = []
    for exp, code in chosen:
        strikes = {k: (c, p) for k, (c, p) in book[code].items() if c or p}
        above = {k: v[0] for k, v in strikes.items() if k >= spot * 0.99 and v[0]}
        below = {k: v[1] for k, v in strikes.items() if k <= spot * 1.01 and v[1]}
        if not above or not below:
            continue
        out.append({"code": code, "expiry": exp.isoformat(), "monthly": len(code) == 6,
                    "call_wall": max(above, key=above.get), "put_wall": max(below, key=below.get),
                    "max_pain": max_pain(strikes), "call_oi": sum(c for c, _ in strikes.values()),
                    "put_oi": sum(p for _, p in strikes.values()),
                    "ladder": [[k, strikes[k][0], strikes[k][1]] for k in sorted(strikes)]})
    return out


def trim_ladder(ladder: list[list[float]], n: int = 14) -> list[list[float]]:
    """Keep the n strikes holding the most open interest (the ones worth drawing), in strike order."""
    return sorted(sorted(ladder, key=lambda r: -(r[1] + r[2]))[:n])


def large_traders(rows: list[dict], contract: str = "TX") -> dict | None:
    """Net position of the ten largest traders across all months of an index future."""
    pool = [r for r in rows if r.get("Contract") == contract and r.get("TypeOfTraders") == "0"]
    # all months combined: TAIFEX's OpenAPI labels it 999912 (the CSV download uses 999999)
    allm = [r for r in pool if r.get("SettlementMonth") in ("999999", "999912")] or \
        sorted((r for r in pool if (r.get("SettlementMonth") or "").isdigit()
                and r.get("SettlementMonth") != "666666"), key=lambda r: r["SettlementMonth"])[:1]
    if not allm:
        return None
    r = allm[0]
    buy, sell, oi = (num(r.get(k)) or 0 for k in ("Top10Buy", "Top10Sell", "OIOfMarket"))
    if not oi:
        return None
    d = r["Date"]
    return {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "net": buy - sell, "oi": oi, "ratio": (buy - sell) / oi}


def futures_basis(rows: list[dict], contract: str = "TX") -> dict | None:
    """Front-month settlement price of an index future (regular session)."""
    front = sorted((r for r in rows if r.get("Contract") == contract and r.get("TradingSession") == "一般"
                    and re.fullmatch(r"\d{6}", r.get("ContractMonth(Week)") or "")
                    and num(r.get("SettlementPrice"))), key=lambda r: r["ContractMonth(Week)"])
    if not front:
        return None
    r = front[0]
    d = r["Date"]
    return {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "month": r["ContractMonth(Week)"],
            "settle": num(r["SettlementPrice"])}


# ----------------------------- investment-trust first buys ----------------------------- #
def trust_first_buys(flow_map: dict[str, list[tuple]], closes: dict[str, float], min_quiet: int = 10,
                     min_value: float = 0.5) -> list[dict]:
    """Stocks the investment trusts have just started buying after a quiet spell.

    flow_map: {code: [(foreign shares, trust shares), ...]} oldest first. A hit needs
    2-6 straight days of trust net buying now, no net-buy day in at least `min_quiet`
    sessions before that, and at least `min_value` (100M TWD) bought in the streak.
    """
    out = []
    for code, series in flow_map.items():
        trust = [x[1] for x in series]
        streak = 0
        for v in reversed(trust):
            if v <= 0:
                break
            streak += 1
        quiet = trust[:len(trust) - streak]
        close = closes.get(code)
        if not 2 <= streak <= 6 or len(quiet) < min_quiet or any(v > 0 for v in quiet) or not close:
            continue
        shares = sum(trust[-streak:])
        value = shares * close / 1e8
        if value < min_value:
            continue
        out.append({"code": code, "streak": streak, "quiet": len(quiet), "lots": shares / 1000, "value": value,
                    "foreign_value": sum(x[0] for x in series[-streak:]) * close / 1e8})
    return sorted(out, key=lambda r: -r["value"])


# ----------------------------- per-stock margin cost (estimate) ----------------------------- #
FINANCING = 0.6  # listed shares are bought on margin with 60% borrowed


def roll_margin_cost(cost: float | None, lots_before: float, lots_now: float, close: float) -> float | None:
    """Average price of the margin position after one more day.

    New lots are assumed bought at that day's close; lots that leave do not change
    the average of what remains. With no prior estimate the day's close is the seed.
    """
    if not close or lots_now <= 0:
        return cost
    if cost is None or lots_before <= 0:
        return close
    added = max(0.0, lots_now - lots_before)
    kept = lots_now - added
    return (cost * kept + close * added) / lots_now


def stock_maintenance(cost: float | None, close: float | None) -> float | None:
    """Estimated maintenance ratio of a stock's margin position: value / loan."""
    return close / (cost * FINANCING) if cost and close else None


def calibrate_costs(book: dict[str, list], closes: dict[str, float], market_maint: float | None,
                    call_line: float = 1.30) -> None:
    """Bring a day's cost estimates in line with what is known, in place.

    book: {code: [average cost, lots, ...]}. First every cost is scaled by one factor
    so the book as a whole shows the market-wide maintenance ratio (which is computed
    from the published loan total, so it is the better-anchored number). Then no
    position is left below the call line: accounts under 130% must top up within
    days or are sold out, so whoever still holds has an effective cost no worse than that.
    """
    if market_maint:
        value = sum(v[1] * closes[c] for c, v in book.items() if closes.get(c))
        loan = sum(v[1] * v[0] * FINANCING for c, v in book.items() if closes.get(c))
        if value and loan:
            k = (value / loan) / market_maint
            if 0.5 < k < 2.0:
                for v in book.values():
                    v[0] *= k
    for c, v in book.items():
        close = closes.get(c)
        if close and v[0] > close / (call_line * FINANCING):
            v[0] = close / (call_line * FINANCING)


# ----------------------------- contract liabilities (customer prepayments) ----------------------------- #
FINMIND = "https://api.finmindtrade.com/api/v4/data"
http.HOST_INTERVAL.setdefault("api.finmindtrade.com", 1.5)


def contract_liabilities(code: str, start: str) -> dict[str, dict]:
    """Quarter-end contract liabilities for one company: {'YYYY-MM-DD': {"value": TWD, "share": % of assets}}."""
    # The free tier is rate-limited per address and answers 402 when busy: fail fast, the caller moves on.
    j = http.get_json(FINMIND, params={"dataset": "TaiwanStockBalanceSheet", "data_id": code, "start_date": start},
                      retries=0, timeout=30)
    out: dict[str, dict] = {}
    for r in j.get("data") or []:
        if r.get("type") == "CurrentContractLiabilities":
            out.setdefault(r["date"], {})["value"] = float(r["value"])
        elif r.get("type") == "CurrentContractLiabilities_per":
            out.setdefault(r["date"], {})["share"] = float(r["value"])
        elif r.get("type") == "TotalAssets":
            out.setdefault(r["date"], {})  # the quarter has been filed even when the line item is absent
    return out


def expected_quarter(today: dt.date) -> str:
    """Latest quarter whose filing deadline has passed (45 days; 90 for the annual report)."""
    ends = [dt.date(today.year - 1, 9, 30), dt.date(today.year - 1, 12, 31), dt.date(today.year, 3, 31),
            dt.date(today.year, 6, 30), dt.date(today.year, 9, 30)]
    due = [e + dt.timedelta(days=90 if e.month == 12 else 46) for e in ends]
    return max(e for e, d in zip(ends, due) if d <= today).isoformat()


def liability_growth(quarters: dict[str, dict]) -> dict | None:
    """Latest contract liabilities against the quarter before (and a year before)."""
    keys = sorted(k for k, v in quarters.items() if v.get("value"))
    if len(keys) < 2:
        return None
    now, prev = quarters[keys[-1]], quarters[keys[-2]]
    out = {"quarter": keys[-1], "value": now["value"], "share": now.get("share"),
           "qoq": now["value"] / prev["value"] - 1, "yoy": None}
    if len(keys) >= 5 and quarters[keys[-5]].get("value"):
        out["yoy"] = now["value"] / quarters[keys[-5]]["value"] - 1
    return out


# ----------------------------- credit and leadership regime ----------------------------- #
def risk_regime(oas: pd.Series, ratio: pd.Series) -> dict | None:
    """Combine US high-yield spreads with semiconductor leadership (SOXX / SPY).

    risk_off: spreads wide (> 4%) or widening fast (+0.5 point in ten sessions) while
    semiconductors lag their 60-day trend. risk_on: spreads tight (< 3.5%) and
    semiconductors leading. Everything else is a rotation, not a liquidity problem.
    """
    oas, ratio = oas.dropna(), ratio.dropna()
    if len(oas) < 15 or len(ratio) < 61:
        return None
    level, chg = float(oas.iloc[-1]), float(oas.iloc[-1] - oas.iloc[-11])
    lead = bool(ratio.iloc[-1] > ratio.tail(60).mean())
    stressed = level > 4.0 or chg >= 0.5
    state = "risk_off" if stressed and not lead else "risk_on" if level < 3.5 and not stressed and lead else "mixed"
    return {"state": state, "oas": level, "oas_chg10": chg, "semis_lead": lead,
            "ratio": float(ratio.iloc[-1]), "ratio_ma60": float(ratio.tail(60).mean())}


def advance_decline(now: dict[str, float], before: dict[str, float]) -> tuple[int, int] | None:
    """Advancers and decliners among common stocks between two days of closes."""
    up = down = 0
    for code, c in now.items():
        b = before.get(code)
        if b and re.fullmatch(r"[1-9]\d{3}", code):
            up += c > b
            down += c < b
    return (up, down) if up + down >= 200 else None


# ----------------------------- dollar liquidity ----------------------------- #
def net_liquidity(walcl: pd.Series, tga: pd.Series, rrp: pd.Series) -> pd.Series:
    """Fed balance sheet minus the Treasury's account minus reverse repos, in trillions of dollars.

    walcl and tga come weekly in millions, rrp daily in billions. What is left is roughly
    the central-bank money actually sitting in the banking system and markets.
    """
    df = pd.concat([walcl.rename("a"), tga.rename("t")], axis=1).dropna()
    r = rrp.dropna()
    df["r"] = r.reindex(r.index.union(df.index)).ffill().reindex(df.index).fillna(0.0)
    return ((df["a"] - df["t"]) / 1e6 - df["r"] / 1e3).rename("net")


# ----------------------------- over-the-counter index ----------------------------- #
def tpex_index() -> dict[str, float]:
    """TPEx (OTC) index closes for the current month: {'YYYY-MM-DD': close}."""
    rows = http.get_json("https://www.tpex.org.tw/openapi/v1/tpex_index")
    return {f"{r['Date'][:4]}-{r['Date'][4:6]}-{r['Date'][6:]}": float(r["Close"]) for r in rows if num(r.get("Close"))}


# ----------------------------- what changed in the picks ----------------------------- #
def pick_changes(now: list[str], earlier: list[dict], today: str) -> dict:
    """Compare this run's picks (symbols, best first) with earlier cohorts.

    Cohorts are grouped by ISO week (the last one of a week stands for it), and only
    weeks before today's count, so re-running within a week still compares with last
    week's list; when there is no earlier week the newest earlier cohort is used.
    Returns {"ref_date", "by_symbol": {sym: {"new", "prev_rank", "move", "weeks"}},
    "dropped": [symbols]}. move > 0 means the pick climbed; weeks counts this week plus
    the unbroken run of earlier weeks in which the symbol was picked.
    """
    week = lambda d: tuple(dt.date.fromisoformat(d).isocalendar()[:2])  # noqa: E731
    older = sorted((c for c in earlier if c["date"] < today), key=lambda c: c["date"])
    weekly: dict[tuple, dict] = {}
    for c in older:
        if week(c["date"]) < week(today):
            weekly[week(c["date"])] = c
    weeks_before = [weekly[w] for w in sorted(weekly)]
    past = weeks_before or older[-1:]
    if not past:
        return {"ref_date": None, "by_symbol": {}, "dropped": []}
    ref = past[-1]
    prev_rank = {p["symbol"]: i + 1 for i, p in enumerate(ref["picks"])}
    out = {}
    for i, sym in enumerate(now):
        weeks = 1
        for c in reversed(weeks_before):  # a same-week reference list does not make it a second week
            if sym not in {p["symbol"] for p in c["picks"]}:
                break
            weeks += 1
        pr = prev_rank.get(sym)
        out[sym] = {"new": pr is None, "prev_rank": pr, "move": None if pr is None else pr - (i + 1), "weeks": weeks}
    return {"ref_date": ref["date"], "by_symbol": out,
            "dropped": [p["symbol"] for p in ref["picks"] if p["symbol"] not in set(now)]}


def previous_week_picks(cohorts: list[dict], today: str) -> set[str]:
    """Symbols in the newest pick list from an earlier ISO week than `today`."""
    week = lambda d: dt.date.fromisoformat(d).isocalendar()[:2]  # noqa: E731
    older = [c for c in cohorts if week(c["date"]) < week(today)]
    return {p["symbol"] for p in max(older, key=lambda c: c["date"])["picks"]} if older else set()


# ----------------------------- TDCC shareholder distribution ----------------------------- #
def parse_tdcc(text: str, codes: set[str] | None = None) -> tuple[str | None, dict[str, list]]:
    """TDCC 集保股權分散表 (CSV) -> (data date, {code: [% held by 1,000+ lot holders,
    % held by 400+ lot holders, number of holders, % held by holders of 50 lots or less]}).

    Levels 12-15 are 400-600, 600-800, 800-1,000 and over 1,000 lots; 16 is a reconciliation
    line and 17 the total.
    """
    import csv
    import io
    date, out = None, {}
    for r in csv.reader(io.StringIO(text.lstrip("\\ufeff"))):
        if len(r) < 6 or not r[2].strip().isdigit():
            continue
        code, lvl = r[1].strip(), int(r[2])
        if codes is not None and code not in codes:
            continue
        date = date or r[0].strip()
        cell = out.setdefault(code, [0.0, 0.0, 0, 0.0])
        pct = num(r[5]) or 0.0
        if lvl == 15:
            cell[0] = pct
        if 12 <= lvl <= 15:
            cell[1] = round(cell[1] + pct, 2)
        if 1 <= lvl <= 8:  # up to 50 lots: the small holders (review 2026-11-14d, C3)
            cell[3] = round(cell[3] + pct, 2)
        if lvl == 17:
            cell[2] = int(num(r[3]) or 0)
    if date and len(date) == 8:
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    return date, out


def tdcc_distribution(codes: set[str]) -> tuple[str | None, dict[str, list]]:
    r = http.request("GET", "https://opendata.tdcc.com.tw/getOD.ashx", params={"id": "1-5"}, timeout=90)
    return parse_tdcc(r.content.decode("utf-8-sig", "replace"), codes)


# ----------------------------- TAIFEX history downloads (CSV, Big5) ----------------------------- #
TAIFEX_WWW = "https://www.taifex.com.tw/cht/3"


def taifex_csv(path: str, start: str, end: str, **extra) -> list[list[str]]:
    """One of TAIFEX's dated CSV downloads between two ISO dates (about a month per request)."""
    r = http.request("POST", f"{TAIFEX_WWW}/{path}", timeout=90,
                     data={"queryStartDate": start.replace("-", "/"), "queryEndDate": end.replace("-", "/"), **extra})
    rows = [[c.strip() for c in line.split(",")] for line in r.content.decode("big5", "replace").splitlines()[1:]]
    return [x for x in rows if len(x) > 3 and re.fullmatch(r"\d{4}/\d{2}/\d{2}", x[0])]


def _iso(d: str) -> str:
    return d.replace("/", "-")


def parse_front_settle(rows: list[list[str]]) -> dict[str, float]:
    """futDataDown (TX): front-month settlement price, regular session, per day."""
    out: dict[str, tuple[str, float]] = {}
    for r in rows:
        if len(r) > 17 and re.fullmatch(r"\d{6}", r[2]) and r[17] == "一般" and num(r[10]):
            d = _iso(r[0])
            if d not in out or r[2] < out[d][0]:
                out[d] = (r[2], num(r[10]))
    return {d: v for d, (_, v) in out.items()}


def parse_total_oi(rows: list[list[str]]) -> dict[str, float]:
    """futDataDown (any contract): open interest summed over all months, regular session."""
    out: dict[str, float] = {}
    for r in rows:
        if len(r) > 17 and re.fullmatch(r"\d{6}", r[2]) and r[17] == "一般" and num(r[11]) is not None:
            out[_iso(r[0])] = out.get(_iso(r[0]), 0.0) + num(r[11])
    return out


def parse_inst_net_oi(rows: list[list[str]], product: str) -> dict[str, float]:
    """futContractsDateDown: the three institutional groups' net open interest in one product."""
    out: dict[str, float] = {}
    for r in rows:
        if len(r) >= 14 and r[1] == product and num(r[13]) is not None:
            out[_iso(r[0])] = out.get(_iso(r[0]), 0.0) + num(r[13])
    return out


def parse_pcr_hist(rows: list[list[str]]) -> dict[str, float]:
    """pcRatioDown: put/call open-interest ratio (1.0 = 100%)."""
    return {_iso(r[0]): num(r[6]) / 100 for r in rows if len(r) > 6 and num(r[6])}


def parse_top10_hist(rows: list[list[str]], contract: str = "TX") -> dict[str, float]:
    """largeTraderFutDown: ten largest traders' net position across all months, share of open interest."""
    out = {}
    for r in rows:
        if len(r) > 9 and r[1] == contract and r[3] == "999999" and r[4] == "0":
            buy, sell, oi = num(r[7]), num(r[8]), num(r[9])
            if oi:
                out[_iso(r[0])] = (buy - sell) / oi
    return out


def parse_foreign_options(rows: list[list[str]]) -> dict[str, dict]:
    """callsAndPutsDateDown (TXO): foreign investors' net open interest in calls and in puts."""
    out: dict[str, dict] = {}
    for r in rows:
        if len(r) > 14 and r[1] == "臺指選擇權" and r[3].startswith("外資") and num(r[14]) is not None:
            out.setdefault(_iso(r[0]), {})["call" if r[2].upper() == "CALL" else "put"] = num(r[14])
    return {d: v for d, v in out.items() if len(v) == 2}


def options_stance(call_net: float, put_net: float) -> int:
    """Long calls and short puts both profit from a rise: +1 bullish, -1 bearish, 0 mixed."""
    if call_net > 0 and put_net < 0:
        return 1
    if call_net < 0 and put_net > 0:
        return -1
    return 0


def derivatives_window(start: str, end: str) -> dict[str, dict]:
    """Every derivatives series the site charts, for one window (six CSV requests).
    Returns {date: {"settle", "pcr_oi", "top10", "retail_mtx", "fo_call", "fo_put"}}."""
    out: dict[str, dict] = {}
    put = lambda d, k, v: out.setdefault(d, {}).__setitem__(k, round(v, 4))  # noqa: E731
    for d, v in parse_front_settle(taifex_csv("futDataDown", start, end, down_type="1", commodity_id="TX")).items():
        put(d, "settle", v)
    for d, v in parse_pcr_hist(taifex_csv("pcRatioDown", start, end)).items():
        put(d, "pcr_oi", v)
    for d, v in parse_top10_hist(taifex_csv("largeTraderFutDown", start, end)).items():
        put(d, "top10", v)
    for d, v in parse_foreign_options(taifex_csv("callsAndPutsDateDown", start, end, commodityId="TXO")).items():
        put(d, "fo_call", v["call"])
        put(d, "fo_put", v["put"])
    inst = parse_inst_net_oi(taifex_csv("futContractsDateDown", start, end, commodityId="MXF"), "小型臺指期貨")
    total = parse_total_oi(taifex_csv("futDataDown", start, end, down_type="1", commodity_id="MTX"))
    for d, n in inst.items():
        if total.get(d):
            put(d, "retail_mtx", -n / total[d])
    return out


# ----------------------------- following the picks: rules fixed in advance ----------------------------- #
TRACK_COST = {"TW": 0.00585, "US": 0.001}  # round trip: TW fee 0.1425% x 2 + 0.3% tax; US a small spread
TRACK_MIN_WEEKS = 26


def _open_after(f: pd.DataFrame, day: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    nxt = f[f.index > day]
    if nxt.empty:
        return None
    px = nxt["open"].iloc[0] if "open" in nxt and nxt["open"].iloc[0] > 0 else nxt["close"].iloc[0]
    return nxt.index[0], float(px)


def follow_track(cohorts: list[dict], frames: dict[str, pd.DataFrame], bench: dict[str, str],
                 universe: dict[str, list[str]]) -> dict:
    """What following each week's list would have earned (review 2026-11-14, quant-7 + risk-5).

    Rules, fixed before any week was scored (cost rule corrected in review 2026-11-14d, C10j):
    hold every pick from the open of the session after the list date, equal weight, until the open of
    the session after the next list's date (the last list is marked to the latest close once five
    sessions have passed). Names that stay on the list are not traded and cost nothing; each name
    bought costs a round trip (TRACK_COST), so a list pays TRACK_COST x (names bought / names held).
    A Taiwan name whose entry open is already limit-up (>= 9.5% above the list-date close) counts as
    not bought: cash for that week. Returns are compared with the market's index fund and with the
    median stock of the market's current universe over the same window. Each row also carries fixed
    horizons for the list as a whole (T+5/T+10/T+20 sessions from the entry open, net of a round trip,
    minus the index fund) and the share of names that beat it at T+20. Until TRACK_MIN_WEEKS lists have
    been scored the verdict is "too few weeks"; after that a mean weekly excess with t < 1 is reported
    as no edge shown yet."""
    rows = []
    held: dict[str, set] = {}
    for i, c in enumerate(cohorts):
        d0 = pd.Timestamp(c["date"])
        d1 = pd.Timestamp(cohorts[i + 1]["date"]) if i + 1 < len(cohorts) else None
        for mk in sorted({p["market"] for p in c["picks"]}):
            b = frames.get(bench.get(mk, ""))
            if b is None:
                continue
            win = []

            def window(f: pd.DataFrame) -> float | None:
                a = _open_after(f, d0)
                if a is None:
                    return None
                z = _open_after(f, d1) if d1 is not None else None
                if z is None:
                    if d1 is not None or (f.index > a[0]).sum() < 5:
                        return None
                    z = (f.index[-1], float(f["close"].iloc[-1]))
                win.append((a[0], z[0]))
                return z[1] / a[1] - 1

            prev = held.get(mk, set())
            now, rets, skipped, bought = set(), [], 0, 0
            for p in c["picks"]:
                if p["market"] != mk or p["symbol"] not in frames:
                    continue
                f = frames[p["symbol"]]
                r = window(f)
                if r is None:
                    continue
                new = p["symbol"] not in prev
                if new and mk == "TW":
                    a = _open_after(f, d0)
                    ref = p.get("close") or _close_on(f, d0)
                    if a is not None and ref and a[1] >= ref * 1.095:
                        rets.append(0.0)  # limit-up at the open: could not be bought, held as cash
                        skipped += 1
                        continue
                now.add(p["symbol"])
                bought += new
                rets.append(r)
            held[mk] = now
            br = window(b)
            if not rets or br is None:
                continue
            uni = [r for s in universe.get(mk, []) if s in frames for r in [window(frames[s])] if r is not None]
            med = float(np.median(uni)) if uni else None
            net = float(np.mean(rets)) - TRACK_COST[mk] * bought / len(rets)
            row = {"date": c["date"], "market": mk, "n": len(rets), "new": bought, "limit_skip": skipped,
                   "net": round(net, 4), "bench": round(br, 4), "excess": round(net - br, 4),
                   "beat_median": round(sum(r > med for r in rets) / len(rets), 3) if med is not None else None,
                   "median": None if med is None else round(med, 4),
                   "exit": max(w[1] for w in win).strftime("%Y-%m-%d") if win else None,
                   "open": d1 is None}
            row.update(_fixed_horizons([frames[p["symbol"]] for p in c["picks"]
                                        if p["market"] == mk and p["symbol"] in frames], b, d0, TRACK_COST[mk]))
            rows.append(row)
    summary = {}
    for mk in ("TW", "US"):
        done = [r for r in rows if r["market"] == mk and not r["open"]]
        ex = np.array([r["excess"] for r in done])
        t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if len(ex) >= 3 and ex.std(ddof=1) > 0 else None
        growth = float(np.prod([1 + r["net"] for r in done]) - 1) if done else None
        bgrowth = float(np.prod([1 + r["bench"] for r in done]) - 1) if done else None
        verdict = (f"樣本不足（{len(done)}／{TRACK_MIN_WEEKS} 週）" if len(done) < TRACK_MIN_WEEKS else
                   "實際追蹤尚未顯示優勢" if t is None or t < 1 else f"實際追蹤顯示正超額（t = {t:.1f}）")
        curve = np.cumprod([1 + r["net"] for r in done]) if done else np.array([])
        t20 = [r["t20"] for r in rows if r["market"] == mk and r.get("t20") is not None]
        summary[mk] = {"weeks": len(done), "mean_excess": round(float(ex.mean()), 4) if len(ex) else None,
                       "t": None if t is None else round(t, 2), "growth": growth, "bench_growth": bgrowth,
                       "verdict": verdict,
                       "max_dd": round(float((curve / np.maximum.accumulate(curve) - 1).min()), 4) if len(curve) else None,
                       "beat_share": round(sum(x > 0 for x in t20) / len(t20), 3) if t20 else None,
                       "t20_mean": round(float(np.mean(t20)), 4) if t20 else None, "t20_weeks": len(t20)}
    return {"rows": rows, "summary": summary, "rules": {"cost": TRACK_COST, "min_weeks": TRACK_MIN_WEEKS,
                                                        "cost_rule": "only names bought", "limit_up": 0.095}}


def _close_on(f: pd.DataFrame, day: pd.Timestamp) -> float | None:
    s = f["close"][f.index <= day]
    return float(s.iloc[-1]) if len(s) else None


def _fixed_horizons(fs: list[pd.DataFrame], bench: pd.DataFrame, d0: pd.Timestamp, cost: float,
                    horizons: tuple = (5, 10, 20)) -> dict:
    """The list as a whole held a fixed number of sessions from the entry open: mean return net of a
    round trip minus the index fund (t5/t10/t20), and at T+20 the share of names that beat the index
    fund (beat20), the share that gained (win20) and the average gain and loss (avg_win/avg_loss)."""
    def fwd(f: pd.DataFrame, h: int) -> float | None:
        a = _open_after(f, d0)
        if a is None:
            return None
        after = f[f.index >= a[0]]
        return float(after["close"].iloc[h - 1]) / a[1] - 1 if len(after) >= h else None
    out = {}
    for h in horizons:
        b = fwd(bench, h)
        rs = [r for f in fs for r in [fwd(f, h)] if r is not None]
        out[f"t{h}"] = round(float(np.mean(rs)) - cost - b, 4) if rs and b is not None else None
        if h == 20 and rs and b is not None:
            net = [r - cost for r in rs]
            wins, losses = [x for x in net if x > 0], [x for x in net if x <= 0]
            out.update({"beat20": round(sum(r - cost > b for r in rs) / len(rs), 3),
                        "win20": round(len(wins) / len(net), 3),
                        "avg_win": round(float(np.mean(wins)), 4) if wins else None,
                        "avg_loss": round(float(np.mean(losses)), 4) if losses else None})
    return out


def entry_info(cohorts: list[dict], symbol: str) -> dict | None:
    """Start of the current unbroken run of weekly lists that contain `symbol`: the list date and the
    close on it (review 2026-11-14d, C10g - shown instead of an "entry price" or a target)."""
    start = None
    for c in sorted(cohorts, key=lambda c: c["date"]):
        hit = next((p for p in c["picks"] if p["symbol"] == symbol), None)
        if hit is None:
            start = None
        elif start is None:
            start = {"date": c["date"], "close": hit.get("close")}
    return start


# ----------------------------- per-stock flow windows ----------------------------- #
def flow_windows(series: list[tuple]) -> dict:
    """10/20-day foreign and trust nets and 5/10/20-day dealer (own account) nets, in lots, from
    [(foreign, trust, dealer own), ...] in shares, oldest first. A dealer window is None when any of
    its days was archived before the dealer leg was kept. Shown only (review 2026-11-14d, C10c)."""
    out: dict = {}
    for n in (10, 20):
        if len(series) >= n:
            out[f"foreign{n}"] = sum(x[0] for x in series[-n:]) / 1000
            out[f"trust{n}"] = sum(x[1] for x in series[-n:]) / 1000
    for n in (5, 10, 20):
        part = series[-n:] if len(series) >= n else []
        out[f"dealer{n}"] = (sum(x[2] for x in part) / 1000
                             if part and all(len(x) > 2 and x[2] is not None for x in part) else None)
    return out


def flow_grid(r20: float | None, inst20: float | None) -> str | None:
    """Which of four cells a stock sits in over the last 20 sessions: price up or down x foreign + trust
    net buying or selling. A description of what happened, not a stage (accumulation, shakeout and
    distribution only separate afterwards). Review 2026-11-14d, C10f."""
    if r20 is None or inst20 is None:
        return None
    return ("up_" if r20 >= 0 else "dn_") + ("buy" if inst20 >= 0 else "sell")


def divergence_flags(inst5: list[float] | None, adv20: float | None, r5: float | None, r5_median: float | None,
                     close: float | None, hi60: float | None, margin: float | None, margin5: float | None,
                     big400_chg4w: float | None = None, small50_chg4w: float | None = None) -> dict:
    """The four price/flow divergences proposed in review 2026-11-14d (C3), defined before any test and
    archived weekly, never shown or scored until the pre-registered test `chip_divergence` decides.

    inst5: the last five daily foreign + trust nets (shares); adv20: 20-day average volume (shares).
    1 sell_hold: institutions sold on each of the last 5 days, at least 0.15 ADV in total, while the
      5-day return is at or above the market median.
    2 buy_below: institutions bought on each of the last 5 days, at least 0.15 ADV in total, while the
      close is below the prior 60-day high.
    3 holders: TDCC holders of 400+ lots up >= 1 point over 4 weeks while holders of <= 50 lots lose >= 1.
    4 margin_hold: margin balance down >= 3% over 5 days (from >= 200 lots) while the price is up > 1%."""
    out: dict = {"sell_hold": None, "buy_below": None, "holders": None, "margin_hold": None}
    if inst5 and len(inst5) == 5 and adv20:
        tot = sum(inst5) / adv20
        if r5 is not None and r5_median is not None:
            out["sell_hold"] = all(x < 0 for x in inst5) and tot <= -0.15 and r5 >= r5_median
        if close is not None and hi60 is not None:
            out["buy_below"] = all(x > 0 for x in inst5) and tot >= 0.15 and close < hi60
    if big400_chg4w is not None and small50_chg4w is not None:
        out["holders"] = big400_chg4w >= 1 and small50_chg4w <= -1
    if margin is not None and margin5 is not None and margin5 >= 200 and r5 is not None:
        out["margin_hold"] = margin / margin5 - 1 <= -0.03 and r5 > 0.01
    return out


# ----------------------------- after a podcast mention ----------------------------- #
def mention_drift(events: list[tuple[str, str]], frames: dict[str, pd.DataFrame], market: dict[str, str],
                  bench: dict[str, str], fresh_days: int = 28, min_n: int = 100,
                  horizons: tuple = (5, 20)) -> dict:
    """What happened after a tracked show mentioned a stock (review 2026-11-14d, G4).

    events: (episode date 'YYYY-MM-DD', symbol). An event is a symbol's first mention by any tracked
    show in `fresh_days` days. Entry is the open of the first session after the episode date, exit the
    close of the h-th session; the excess is against the market's index fund (0050 / SPY) over the same
    sessions. Also reported: the excess minus each stock's usual excess (its mean over every Wednesday in
    the price store, same rule), because today's popular stocks beat the index anyway. t uses month
    clusters. With fewer than `min_n` events a cell gives only the count."""
    seen: dict[str, pd.Timestamp] = {}
    first = []
    for d, s in sorted(events):
        ts = pd.Timestamp(d)
        if s in seen and (ts - seen[s]).days < fresh_days:
            seen[s] = ts
            continue
        seen[s] = ts
        first.append((ts, s))

    def excess(f: pd.DataFrame, b: pd.DataFrame, ts: pd.Timestamp, h: int) -> float | None:
        a, ab = _open_after(f, ts), _open_after(b, ts)
        if a is None or ab is None:
            return None
        fa, ba = f[f.index >= a[0]], b[b.index >= ab[0]]
        if len(fa) < h or len(ba) < h:
            return None
        return (float(fa["close"].iloc[h - 1]) / a[1] - 1) - (float(ba["close"].iloc[h - 1]) / ab[1] - 1)

    usual: dict[tuple[str, int], float | None] = {}

    def usual_excess(s: str, h: int) -> float | None:
        if (s, h) not in usual:  # the same rule from every fifth session, vectorised
            f, b = frames[s], frames[bench[market[s]]]
            idx = f.index.intersection(b.index)
            op = lambda df: df["open"].reindex(idx).where(lambda v: v > 0)  # noqa: E731
            x = ((f["close"].reindex(idx).shift(-h) / op(f).shift(-1) - 1)
                 - (b["close"].reindex(idx).shift(-h) / op(b).shift(-1) - 1)).iloc[::5].dropna()
            usual[(s, h)] = float(x.mean()) if len(x) >= 20 else None
        return usual[(s, h)]

    out: dict = {"fresh_days": fresh_days, "min_n": min_n, "cells": []}
    for mk in ("TW", "US"):
        b = frames.get(bench.get(mk, ""))
        if b is None:
            continue
        for h in horizons:
            xs, adj, months = [], [], []
            for ts, s in first:
                if market.get(s) != mk or s not in frames:
                    continue
                x = excess(frames[s], b, ts, h)
                if x is None:
                    continue
                xs.append(x)
                u = usual_excess(s, h)
                adj.append(None if u is None else x - u)
                months.append(ts.strftime("%Y-%m"))
            cell = {"market": mk, "h": h, "n": len(xs)}
            if len(xs) >= min_n:
                a = np.array(xs)
                adj_ok = [(v, m) for v, m in zip(adj, months) if v is not None]
                cell.update({"mean": round(float(a.mean()), 4), "median": round(float(np.median(a)), 4),
                             "pos": round(float((a > 0).mean()), 3), "t": _cluster_t(a, months),
                             "adj": round(float(np.mean([v for v, _ in adj_ok])), 4) if adj_ok else None,
                             "adj_t": _cluster_t(np.array([v for v, _ in adj_ok]), [m for _, m in adj_ok]) if adj_ok else None})
            out["cells"].append(cell)
    out["events"] = len(first)
    return out


def _cluster_t(x: np.ndarray, groups: list[str]) -> float | None:
    """t of the mean with standard errors clustered by group (months: events in the same month share
    the market's moves)."""
    if len(x) < 3:
        return None
    m = float(x.mean())
    by: dict[str, float] = {}
    for v, g in zip(x, groups):
        by[g] = by.get(g, 0.0) + (float(v) - m)
    se = float(np.sqrt(sum(v * v for v in by.values()))) / len(x)
    return round(m / se, 2) if se > 0 else None


def valuation_history(code: str, start: str) -> dict[str, list]:
    """Month-end P/E and P/B for one Taiwan company since `start`, from FinMind's TaiwanStockPER
    (third party; used only for the one-time back-fill - later months come from the exchanges)."""
    j = http.get_json(FINMIND, params={"dataset": "TaiwanStockPER", "data_id": code, "start_date": start},
                      retries=0, timeout=30)
    out: dict[str, list] = {}
    for r in j.get("data") or []:  # daily rows in date order: the last one of each month wins
        out[r["date"][:7]] = [r.get("PER") or None, r.get("PBR") or None]
    if not out:
        raise RuntimeError(f"no valuation history for {code}")
    return out
