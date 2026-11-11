"""Leading indicators: derivatives positioning, cross-asset spreads, breadth, revenue momentum.

Fetchers are thin; everything that computes is a pure function so it can be tested
without the network. Thresholds quoted in the readings are common market rules of
thumb, shown as context - none of them has been validated on this site's own data.
"""
from __future__ import annotations

import datetime as dt
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
    allm = [r for r in pool if r.get("SettlementMonth") == "999999"] or \
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
