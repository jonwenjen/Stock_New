"""Weekly build: fetch every source, score, forecast, write docs/data/*.json.

Run:  python -m pipeline.build            (full run)
      QUICK=1 python -m pipeline.build    (small universe, short back-fill; for smoke tests)

Every source is wrapped in `safe()`: one failing website never aborts the run,
and the outcome of each source is published in summary.json -> sources.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd

from . import funds, http, indicators, leading, media, prices, scoring, twse, us_data

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
CHARTS = DATA / "charts"
CFG = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
CUSIP_CACHE = ROOT / "config" / "cusip_cache.json"
QUICK = os.environ.get("QUICK") == "1"
# SEC asks automated clients to say who they are ("name contact"); the owner's contact is in the config.
http.SEC_UA = os.environ.get("SEC_USER_AGENT") or CFG.get("sec_contact") or http.SEC_UA
TPE = dt.timezone(dt.timedelta(hours=8))

AMBIGUOUS_TICKERS = {"NOW", "ARM", "DIS", "CAT", "COST", "GEV", "CEG"}

SOURCES: list[dict] = []


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def safe(name: str, fn, default=None, url: str = ""):
    """Run one data source; record success/failure instead of raising."""
    t0 = time.time()
    try:
        res = fn()
        SOURCES.append({"name": name, "ok": True, "url": url, "note": ""})
        log(f"ok   {name} ({time.time() - t0:.1f}s)")
        return res
    except Exception as e:  # noqa: BLE001 - any source may fail in any way
        SOURCES.append({"name": name, "ok": False, "url": url, "note": str(e)[:160]})
        log(f"FAIL {name}: {e}")
        return default


def retry(fn, attempts: int = 3, wait: float = 8.0):
    """For endpoints that occasionally answer 200 with an empty or truncated body."""
    for i in range(attempts):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if i == attempts - 1:
                raise
            time.sleep(wait)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(obj), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _clean(o):
    """NaN/inf are not valid JSON -> null; round floats to keep files small."""
    if isinstance(o, float):
        return round(o, 4) if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):  # numpy scalar
        return _clean(o.item())
    return o


def fname(symbol: str) -> str:
    """Symbol -> chart file stem ('2330.TW' -> '2330_TW', 'F-B09,005' -> 'F-B09_005')."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", symbol)


def ret(df: pd.DataFrame, n: int) -> float | None:
    return float(df["close"].iloc[-1] / df["close"].iloc[-1 - n] - 1) if len(df) > n else None


def ytd(df: pd.DataFrame) -> float | None:
    prev = df[df.index.year < df.index[-1].year]
    return float(df["close"].iloc[-1] / prev["close"].iloc[-1] - 1) if len(prev) else None


# --------------------------------------------------------------------------- #
# Market-wide Taiwan flows (persisted history, appended every run)
# --------------------------------------------------------------------------- #
def update_flow_history(trading_days: list[str], twii: pd.DataFrame) -> list[dict]:
    path = DATA / "history" / "tw_flows.json"
    hist = {r["date"]: r for r in read_json(path, [])}
    window = trading_days[-(8 if QUICK else CFG["flow_history_days"]):]
    todo_inst = [d for d in window if "foreign" not in hist.get(d, {})]
    todo_margin = [d for d in window if "margin_bal" not in hist.get(d, {})]
    log(f"flow history: {len(todo_inst)} inst days, {len(todo_margin)} margin days to fetch")
    ok = 0
    for todo, fn, label in ((todo_inst, twse.inst_market, "inst"), (todo_margin, twse.margin_market, "margin")):
        for d, row in twse.fetch_days(todo, fn, label, log).items():
            hist.setdefault(d, {"date": d}).update(row)
            ok += 1
        write_json(path, [hist[d] for d in sorted(hist)])  # checkpoint after each phase
    fail = len(todo_inst) + len(todo_margin) - ok
    closes = {d.strftime("%Y-%m-%d"): float(c) for d, c in twii["close"].items()}
    for d, r in hist.items():
        if d in closes:
            r["twii"] = round(closes[d], 2)
    rows = [hist[d] for d in sorted(hist) if "foreign" in hist[d] or "margin_bal" in hist[d]]
    write_json(path, rows)
    if not rows:
        raise RuntimeError("no flow data available")
    log(f"flow history: +{ok} ok, {fail} failed, {len(rows)} rows total")
    return rows


def seed_costs(first_day: str) -> dict[str, float]:
    """Average close over up to 60 cached sessions before first_day: the starting guess
    for what margin buyers paid. Local cache only - a missing cache just means no seed."""
    files = sorted(f for f in DAY_CACHE.glob("*.json") if f.stem < first_day)[-60:] if DAY_CACHE.exists() else []
    if len(files) < 5:
        return {}
    tot: dict[str, list[float]] = {}
    for f in files:
        for code, bar in read_json(f, {}).items():
            cell = tot.setdefault(code, [0.0, 0])
            cell[0] += bar[3]
            cell[1] += 1
    return {c: v[0] / v[1] for c, v in tot.items()}


MARGIN_CACHE = ROOT / ".cache" / "twse_margin"  # local only, like DAY_CACHE


def cached_margin(date: str) -> dict:
    path = MARGIN_CACHE / f"{date}.json"
    if path.exists():
        return read_json(path, {})
    data = twse.margin_stocks(date)
    write_json(path, data)
    return data


def roll_margin_costs(store: dict, tables: dict[str, dict], days: list[str], maint: dict[str, float]) -> dict:
    """Advance the per-stock margin cost estimate through `days` (oldest first).

    store: {"date": last day applied, "stocks": {code: [average cost, lots, last close]}}.
    maint: {date: market-wide maintenance ratio}, used to calibrate each day's book.
    """
    stocks = store.setdefault("stocks", {})
    for d in days:
        if d not in tables or twse.THROTTLED and not (DAY_CACHE / f"{d}.json").exists():
            continue
        try:
            closes = {c: bar[3] for c, bar in cached_day(d).items()}
        except Exception as e:  # noqa: BLE001 - no quote table for that day yet
            log(f"  margin cost {d}: {e}")
            continue
        seed = seed_costs(d) if not stocks else {}
        for code, m in tables[d].items():
            lots, close = m.get("margin") or 0, closes.get(code)
            if not twse.COMMON_STOCK.match(code):
                continue
            if lots <= 0:
                stocks.pop(code, None)
                continue
            prev = stocks.get(code)
            if prev and close and len(prev) > 2 and prev[2] and not 0.85 < close / prev[2] < 1.15:
                prev[0] *= close / prev[2]  # beyond the daily limit: a split or rights issue, not a price move
            if prev:
                cost = leading.roll_margin_cost(prev[0], prev[1], lots, close)
            else:  # a seed far from today's price is a corporate action in the history, not information
                cost = seed.get(code) if close and 0.5 < (seed.get(code) or 0) / close < 2.0 else close
            if cost:
                stocks[code] = [cost, lots, close or (prev[2] if prev and len(prev) > 2 else None)]
        leading.calibrate_costs(stocks, closes, maint.get(d))
        for v in stocks.values():
            v[0] = round(v[0], 2)
        store["date"] = d
    return store


def update_maintenance(flows: list[dict]) -> tuple[str | None, dict, dict, dict, dict]:
    """Estimate the market margin maintenance ratio for recent days and store it in the flow history.

    Needs, per day, every security's margin balance (one request) and close (the daily
    quote table, usually already cached by the price update). Newest days first, so a
    throttled run still gets the latest figure. Returns (reference date ~5 days back,
    that day's per-stock margin table, closes on that day, closes ~20 days back) for
    the per-stock tables.
    """
    rows = [r for r in flows if r.get("margin_bal")]
    if not rows:
        raise RuntimeError("no margin balance history")
    window = rows[-(3 if QUICK else CFG["maint_days"]):]
    ref_date = rows[-6]["date"] if len(rows) >= 6 else None
    need = [r["date"] for r in window if "maint" not in r]
    cost_path = DATA / "history" / "tw_margin_cost.json"
    cost_store = read_json(cost_path, {})
    cost_days = [r["date"] for r in window if r["date"] > cost_store.get("date", "")]
    dates = list(dict.fromkeys(need[::-1] + cost_days[::-1] + ([ref_date] if ref_date else [])))
    tables = twse.fetch_days(dates, cached_margin, "margin-stocks", log)
    by_date = {r["date"]: r for r in rows}
    done = 0
    for d in need[::-1]:
        if d not in tables or twse.THROTTLED and not (DAY_CACHE / f"{d}.json").exists():
            continue
        try:
            closes = {c: bar[3] for c, bar in cached_day(d).items()}
        except Exception as e:  # noqa: BLE001 - no quote table for that day yet
            log(f"  maintenance {d}: {e}")
            continue
        m = twse.maintenance_ratio(tables[d], closes, by_date[d]["margin_bal"] * 1e8)
        if m:
            by_date[d].update({"maint": round(m["maint"], 4), "collateral": round(m["collateral"], 1)})
            done += 1
    write_json(DATA / "history" / "tw_flows.json", flows)
    log(f"maintenance ratio: {done}/{len(need)} days computed")
    if cost_days:
        cost_store = roll_margin_costs(cost_store, tables, cost_days,
                                       {r["date"]: r["maint"] for r in rows if r.get("maint")})
        write_json(cost_path, cost_store)
        log(f"margin cost estimate: {len(cost_store.get('stocks', {}))} stocks through {cost_store.get('date')}")
    def closes_on(d: str | None) -> dict:  # from the local day cache only: never worth an extra request
        path = DAY_CACHE / f"{d}.json"
        return {c: bar[3] for c, bar in read_json(path, {}).items()} if d and path.exists() else {}

    return (ref_date, tables.get(ref_date, {}), closes_on(ref_date), closes_on(window[0]["date"]),
            cost_store.get("stocks", {}))


def stock_flows(trading_days: list[str]) -> tuple[list[str], dict[str, list[tuple]]]:
    """Per-stock net shares (foreign, trust) for the latest N trading days.

    Kept in history/tw_stock_flows.json (in lots) so a weekly run only asks TWSE
    for the ~5 new days. Returns (dates, {code: [(foreign, trust), ...]}) in shares.
    """
    path = DATA / "history" / "tw_stock_flows.json"
    store: dict[str, dict] = read_json(path, {})
    n = 5 if QUICK else CFG["stock_flow_days"]
    need = [d for d in trading_days[-n:] if d not in store]
    for d, table in twse.fetch_days(need, twse.inst_stocks, "T86", log).items():
        store[d] = {c: [round(v[0] / 1000), round(v[1] / 1000)] for c, v in table.items()
                    if twse.tracked(c) and (v[0] or v[1])}
    store = {d: store[d] for d in sorted(store)[-(n + 10):]}
    if not store:
        raise RuntimeError("no per-stock institutional data")
    write_json(path, store)
    dates = sorted(store)[-n:]
    codes = set().union(*(set(store[d]) for d in dates))
    return dates, {c: [tuple(x * 1000 for x in store[d].get(c, (0, 0))) for d in dates] for c in codes}


# --------------------------------------------------------------------------- #
# Price store: docs/data/charts/<symbol>.json doubles as the persisted history
# --------------------------------------------------------------------------- #
SOURCE_URL = {"cboe": "https://www.cboe.com", "fred": "https://fred.stlouisfed.org",
              "twse": "https://www.twse.com.tw"}
DEEP: dict[str, bool] = {}  # symbols whose pre-TWSE history was filled from Yahoo this run


def load_store(symbol: str) -> tuple[pd.DataFrame, dict]:
    payload = read_json(CHARTS / f"{fname(symbol)}.json", {})
    return prices.rows_to_frame(payload.get("d") or []), payload


def months_between(start: dt.date, end: dt.date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch_index(ix: dict, intraday: dict) -> pd.DataFrame:
    sym, scale = ix["symbol"], ix.get("scale", 1)
    if ix["source"] == "fred":
        return prices.fred_daily(ix["code"])
    if ix["source"] == "cboe":
        raw = []
        try:
            raw = prices.cboe_intraday_raw(ix["code"])
        except Exception:  # noqa: BLE001 - intraday is optional
            pass
        intraday[sym] = prices.intraday_5m(raw, scale)
        return prices.append_session(prices.cboe_daily(ix["code"], scale), raw, scale)
    # TAIEX: stored history + official monthly OHLC tables for the months since the last bar.
    old, payload = load_store(sym)
    today = dt.datetime.now(TPE).date()
    if len(old) < 250 or not payload.get("deep"):
        y = prices.Yahoo.daily("^TWII")  # optional: deep history in one request
        if y is not None:
            old = with_older_history(old, y)
            DEEP[sym] = True
    # Ask only for months the store does not already hold in full (always the current one).
    back = 7 if QUICK else CFG["taiex_months"]
    have = old.index.strftime("%Y-%m").value_counts() if len(old) else {}
    this_month = today.strftime("%Y-%m")
    months = [m for m in months_between(today.replace(day=1) - dt.timedelta(days=30 * back), today)
              if m == this_month or have.get(m, 0) < 15]
    got = twse.fetch_days(months, twse.taiex_month, "TAIEX", log)
    rows = [r for m in sorted(got) for r in got[m]]
    new = pd.DataFrame([{"open": o, "high": h, "low": l, "close": c, "volume": 0.0} for _, o, h, l, c in rows],
                       index=pd.to_datetime([r[0] for r in rows])) if rows else None
    df = prices.merge(old, new)
    if df.empty:
        raise RuntimeError("no TAIEX data")
    return df


def with_older_history(official: pd.DataFrame | None, yahoo: pd.DataFrame) -> pd.DataFrame:
    """Use Yahoo only for what the official store lacks.

    With official bars on file, Yahoo contributes strictly older dates. Without any,
    it seeds the store (weekend-stamped rows are dropped; official bars later override).
    """
    if official is not None and len(official):
        return prices.merge(yahoo[yahoo.index < official.index[0]], official)
    return yahoo[yahoo.index.dayofweek < 5]


DAY_CACHE = ROOT / ".cache" / "twse_days"  # local only (git-ignored); speeds up re-runs


def cached_day(date: str) -> dict:
    path = DAY_CACHE / f"{date}.json"
    if path.exists():
        return read_json(path, {})
    data = twse.day_quotes(date)
    write_json(path, data)
    return data


def update_tw_prices(trading_days: list[str], universe: list[str]) -> dict[str, pd.DataFrame]:
    """Bring the Taiwan price store up to date.

    1. Stocks without deep history get it from Yahoo in one request each (optional).
    2. Trading days the store does not cover come from TWSE's official daily table
       (one request covers every listed stock). At most MAX_DAYS per run, newest
       first, so a cold start without Yahoo fills in over a few runs instead of
       hammering TWSE.
    """
    stored: dict[str, pd.DataFrame] = {}
    deep: set[str] = set()
    for p in CHARTS.glob("*_TW.json"):
        code = p.stem[:-3]
        f, payload = load_store(code + ".TW")
        if len(f):
            stored[code] = f
            if payload.get("deep"):
                deep.add(code)
    for code in universe:
        if code in deep:
            continue
        y = prices.Yahoo.daily(f"{code}.TW")
        if y is not None and len(y):
            stored[code] = with_older_history(stored.get(code), y)
            DEEP[f"{code}.TW"] = True

    window = trading_days[-(125 if QUICK else CFG["tw_price_days"]):]
    have = {d: 0 for d in window}
    for f in stored.values():
        for d in f.index[-len(window):].strftime("%Y-%m-%d"):
            if d in have:
                have[d] += 1
    # A day is "covered" only when the stocks we are about to score have it too - otherwise a
    # handful of well-stocked names would hide the fact that the rest of the universe lacks it.
    uni = [c for c in universe if c in stored]
    uni_have = {d: 0 for d in window}
    for c in uni:
        for d in stored[c].index[-len(window):].strftime("%Y-%m-%d"):
            if d in uni_have:
                uni_have[d] += 1
    recent = set(window[-10:])
    need = [d for d in window if not stored or have[d] < 0.8 * len(stored)
            or (d in recent and uni_have[d] < len(universe))]
    max_days = 20 if QUICK else int(os.environ.get("TW_MAX_DAYS", 120))
    log(f"tw prices: {len(stored)} stocks on file, {len(need)} days missing (fetching up to {max_days})")
    need = need[-max_days:] if max_days > 0 else []
    # Days already in the local cache cost nothing; the rest are requested newest first so
    # that a throttled run still ends with the most recent (most valuable) days on file.
    tables = {d: read_json(DAY_CACHE / f"{d}.json", {}) for d in need if (DAY_CACHE / f"{d}.json").exists()}
    todo = [d for d in reversed(need) if d not in tables]
    tables.update(twse.fetch_days(todo, cached_day, "quotes", log))
    day_rows: dict[str, list] = {}
    for d, table in sorted(tables.items()):
        for code, bar in table.items():
            if twse.tracked(code):  # the day tables hold every security; the price store keeps ours
                day_rows.setdefault(code, []).append((d, *bar))
    out: dict[str, pd.DataFrame] = {}
    # Stocks that only just entered the universe have no history on file. Back-fill a
    # few of them per run, month by month (13 requests each), unless this run was
    # already a bulk catch-up.
    budget = 0 if QUICK or len(todo) > 30 else 4
    for code in dict.fromkeys(list(stored) + universe):
        rows = day_rows.get(code)
        new = pd.DataFrame([r[1:] for r in rows], columns=prices.COLS,
                           index=pd.to_datetime([r[0] for r in rows])) if rows else None
        df = prices.merge(stored.get(code), new)
        if code in universe and len(df) < 60 and budget > 0 and not twse.THROTTLED:
            budget -= 1
            end = dt.date.fromisoformat(trading_days[-1])
            got = twse.fetch_days(months_between(end - dt.timedelta(days=365), end),
                                  lambda m, c=code: twse.stock_month(c, m), f"backfill {code}", log)
            bars = [r for m in sorted(got) for r in got[m]]
            if bars:
                df = prices.merge(pd.DataFrame([b[1:] for b in bars], columns=prices.COLS,
                                               index=pd.to_datetime([b[0] for b in bars])), df)
        if len(df):
            out[code] = df
    if not out:
        raise RuntimeError("no Taiwan price data")
    for code, df in out.items():  # checkpoint now: a later failure must not waste the download
        sym = f"{code}.TW"
        prev = load_store(sym)[1]
        payload = prices.compact(df, 5, prev.get("w"))
        payload.update({"symbol": sym, "name": prev.get("name") or code, "i": prev.get("i") or [],
                        "deep": bool(prev.get("deep") or DEEP.get(sym))})
        write_json(CHARTS / f"{fname(sym)}.json", payload)
    return out


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:  # noqa: C901, PLR0912, PLR0915 - a linear pipeline reads best top-to-bottom
    now = dt.datetime.now(TPE)
    today = now.date()
    log(f"build start {now.isoformat(timespec='seconds')} quick={QUICK}")
    prev_smart = read_json(DATA / "smart_money.json", {})
    prev_pod = read_json(DATA / "podcasts.json", {})

    # ---- 1. indices (official sources; stored history is the fallback) -------
    frames: dict[str, pd.DataFrame] = {}
    meta: dict[str, dict] = {}
    intraday: dict[str, list] = {}
    stale: list[str] = []
    for ix in CFG["indices"]:
        sym = ix["symbol"]
        # One more try after a pause: the quote hosts answer 429 to short bursts and recover quickly.
        df = safe(f"{ix['source'].upper()} 指數 {ix['name']}",
                  lambda i=ix: retry(lambda: fetch_index(i, intraday), attempts=2, wait=40),
                  url=SOURCE_URL[ix["source"]])
        if df is None or len(df) < 120:
            df, _ = load_store(sym)  # keep last week's history rather than dropping the series
            if len(df):
                stale.append(sym)
        if len(df) >= 120:
            frames[sym] = df
            meta[sym] = {"name": ix["name"], "market": ix["market"], "kind": "index"}
    if "TWII" not in frames or "SPX" not in frames:
        log("core index data unavailable - aborting without touching existing data")
        return 1
    twii = frames["TWII"]
    trading_days = [d.strftime("%Y-%m-%d") for d in twii.index]

    # ---- 2. Taiwan snapshots ------------------------------------------------
    tw_api = "https://openapi.twse.com.tw"
    # These endpoints occasionally answer 200 with an empty body, hence retry(); if one is
    # still down, last week's per-stock values (kept in universe.json) stand in for it.
    prev_tw = {r["code"]: r for r in read_json(DATA / "universe.json", []) if r.get("market") == "TW"}
    carry = lambda keys: {c: {k: r.get(k) for k in keys} for c, r in prev_tw.items()}  # noqa: E731
    quotes = safe("證交所 每日收盤行情", lambda: retry(twse.quotes), {}, tw_api)
    valuation = safe("證交所 本益比/殖利率/淨值比", lambda: retry(twse.valuation), None, tw_api)
    revenue = safe("證交所 月營收", lambda: retry(twse.monthly_revenue), None, tw_api)
    eps = safe("證交所 每股盈餘", lambda: retry(twse.quarterly_eps), None, tw_api)
    margin_now = safe("證交所 個股融資融券", lambda: retry(twse.margin_latest), {}, tw_api)
    if valuation is None:
        valuation = carry(("pe", "pb", "yield"))
    if revenue is None:
        revenue = carry(("industry", "month", "rev_mom", "rev_yoy", "rev_cum_yoy"))
    if eps is None:
        eps = carry(("eps", "eps_period"))
    insiders = safe("證交所 董監持股", lambda: retry(twse.insiders), {}, tw_api)
    futures = safe("期交所 外資台指期未平倉", twse.foreign_futures, None, "https://openapi.taifex.com.tw")

    # ETFs: a fixed core list plus whatever is trading most actively right now.
    etf_cfg = CFG.get("etfs", {})
    etf_ranked = sorted((c for c in quotes if c.startswith("00") and quotes[c]["value"]),
                        key=lambda c: -quotes[c]["value"])
    tw_etfs = list(dict.fromkeys([c for c in etf_cfg.get("tw_core", []) if c in quotes]
                                 + etf_ranked[:etf_cfg.get("tw_top_traded", 0)]))
    us_etfs = etf_cfg.get("us", [])
    if QUICK:
        tw_etfs, us_etfs = tw_etfs[:3], us_etfs[:3]
    twse.EXTRA_CODES = set(tw_etfs)

    flows = safe("證交所 三大法人/融資 (市場)", lambda: update_flow_history(trading_days, twii),
                 read_json(DATA / "history" / "tw_flows.json", []), "https://www.twse.com.tw")
    sf = safe("證交所 三大法人 (個股)", lambda: stock_flows(trading_days), None, "https://www.twse.com.tw")
    flow_dates, flow_map = sf if sf else ([], {})
    mres = safe("證交所 個股融資 (逐日) 與融資維持率", lambda: update_maintenance(flows), None, "https://www.twse.com.tw")
    margin_ref_date, margin_ref, closes_5, closes_20, margin_cost = mres if mres else (None, {}, {}, {}, {})
    if not margin_cost:  # this week's update failed: last week's estimate is still better than none
        margin_cost = read_json(DATA / "history" / "tw_margin_cost.json", {}).get("stocks", {})
    # The OpenAPI snapshot can trail the dated tables by a session; prefer the newest dated closes.
    spot_close = {c: q["close"] for c, q in quotes.items() if q.get("close")}
    spot_close.update({c: bar[3] for c, bar in read_json(DAY_CACHE / f"{trading_days[-1]}.json", {}).items()})
    trust_first = leading.trust_first_buys({c: v for c, v in flow_map.items() if twse.COMMON_STOCK.match(c)},
                                           spot_close) if len(flow_dates) >= 15 else []
    for r in trust_first:
        r["name"] = quotes[r["code"]]["name"]
    first_by_code = {r["code"]: r for r in trust_first}

    rev_hist = safe("公開資訊觀測站 月營收歷史", lambda: update_revenue_history(revenue), {},
                    "https://mopsov.twse.com.tw")

    # ---- 3. universes -------------------------------------------------------
    forced = [k for k in CFG["aliases"] if k.isdigit()]
    ranked = sorted((c for c in quotes if twse.COMMON_STOCK.match(c) and quotes[c]["value"]),
                    key=lambda c: -quotes[c]["value"])
    n_tw = 12 if QUICK else CFG["tw_universe_size"]
    tw_codes = list(dict.fromkeys(ranked[:n_tw] + [c for c in forced if c in quotes]))
    if not tw_codes:  # TWSE snapshot down -> fall back to the well-known names
        tw_codes = forced
    us_list = CFG["us_universe"][:10] if QUICK else CFG["us_universe"]
    liab = safe("FinMind 合約負債（資產負債表）", lambda: update_contract_liabilities(tw_codes[:5] if QUICK else tw_codes, today),
                {}, "https://finmindtrade.com")

    # ---- 4. prices ----------------------------------------------------------
    tw_frames = safe("證交所 個股每日行情", lambda: update_tw_prices(trading_days, tw_codes + tw_etfs), {},
                     "https://www.twse.com.tw")
    for code, df in tw_frames.items():
        sym = f"{code}.TW"
        name = quotes.get(code, {}).get("name") or load_store(sym)[1].get("name") or code
        frames[sym] = df
        if code in twse.EXTRA_CODES:
            meta[sym] = {"name": name, "market": "TW", "kind": "etf", "code": code, "scored": False,
                         "industry": etf_category(code, name)}
        else:
            meta[sym] = {"name": name, "market": "TW", "kind": "stock", "code": code, "scored": code in tw_codes,
                         "industry": revenue.get(code, {}).get("industry") or "其他"}
    def us_prices(tkr: str) -> pd.DataFrame:
        raw = []
        try:
            raw = prices.cboe_intraday_raw(tkr)
        except Exception:  # noqa: BLE001 - intraday is optional
            pass
        df = prices.append_session(prices.cboe_daily(tkr), raw)
        intraday[tkr] = prices.intraday_5m(raw)
        return df

    us_frames: dict[str, pd.DataFrame] = {}
    later: list[str] = []
    for tkr, _, _ in us_list + us_etfs:
        try:
            us_frames[tkr] = us_prices(tkr)
        except Exception as e:  # noqa: BLE001 - usually a 429 burst limit: try again at the end
            log(f"  price {tkr}: {e} (will retry)")
            later.append(tkr)
    fails = 0
    if later:
        time.sleep(45)
    for tkr in later:
        try:
            us_frames[tkr] = us_prices(tkr)
        except Exception as e:  # noqa: BLE001
            fails += 1
            log(f"  price {tkr}: {e}")
            df, _ = load_store(tkr)
            if len(df):
                us_frames[tkr] = df
                stale.append(tkr)
    for tkr, name, industry in us_list:
        if len(us_frames.get(tkr, [])) >= 120:
            frames[tkr] = us_frames[tkr]
            meta[tkr] = {"name": name, "market": "US", "kind": "stock", "code": tkr, "industry": industry,
                         "scored": True}
    for tkr, name, category in us_etfs:
        if len(us_frames.get(tkr, [])) >= 120 and tkr not in frames:
            frames[tkr] = us_frames[tkr]
            meta[tkr] = {"name": name, "market": "US", "kind": "etf", "code": tkr, "industry": category,
                         "scored": False}
    # Never score a stock on stale prices: its last bar must be within a week of the
    # newest bar in its own market (a history gap would otherwise look like a signal).
    newest = {mk: max((frames[s].index[-1] for s, m in meta.items() if m["kind"] == "stock" and m["market"] == mk),
                      default=None) for mk in ("TW", "US")}
    fresh = lambda s: frames[s].index[-1] >= newest[meta[s]["market"]] - pd.Timedelta(days=7)  # noqa: E731
    stocks = [s for s, m in meta.items()
              if m["kind"] == "stock" and m["scored"] and len(frames[s]) >= 120 and fresh(s)]
    behind = [s for s, m in meta.items() if m["kind"] == "stock" and m["scored"] and not fresh(s)]
    if behind:
        SOURCES.append({"name": "股價過期未評分", "ok": False, "url": "", "note": "、".join(behind)[:160]})
    SOURCES.append({"name": "Cboe 美股每日行情", "ok": fails < len(us_list), "url": "https://www.cboe.com",
                    "note": f"{len(us_list) - fails} 檔成功，{fails} 檔失敗"})
    if stale:
        SOURCES.append({"name": "沿用上週股價", "ok": False, "url": "", "note": "、".join(stale)[:160]})
    log(f"prices: {len(stocks)} scored stocks, {len(frames)} series")

    # ---- 5. US smart money + fundamentals ----------------------------------
    trades = safe("Quiver Quantitative 美國國會議員交易", us_data.congress_trades, None,
                  "https://www.quiverquant.com/congresstrading/")
    congress_ok = trades is not None
    if trades is None:
        trades = (prev_smart.get("congress") or {}).get("trades", [])
    csum = us_data.congress_summary(trades, today)

    cache = read_json(CUSIP_CACHE, {})
    gurus = []
    for inv in (CFG["investors"][:3] if QUICK else CFG["investors"]):
        g = safe(f"SEC 13F {inv['name']}", lambda c=inv["cik"]: us_data.investor_13f(c), None,
                 "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=13F-HR")
        if g:
            gurus.append({**inv, **g})
    if gurus:
        need = [c for g in gurus for c in g["actions"]]
        safe("OpenFIGI CUSIP→代號", lambda: us_data.map_cusips(need, cache), cache, "https://www.openfigi.com")
        write_json(CUSIP_CACHE, dict(sorted(cache.items())))
    else:
        gurus = prev_smart.get("gurus", [])
    guru_net: dict[str, int] = {}
    for g in gurus:
        for row in g.get("top", []) + g.get("exits", []):
            row["ticker"] = cache.get(row.get("cusip", ""), "") or row.get("ticker", "")
        for cusip, action in (g.get("actions") or {}).items():
            t = cache.get(cusip)
            if t and action in ("new", "add", "cut", "exit"):
                guru_net[t] = guru_net.get(t, 0) + (1 if action in ("new", "add") else -1)

    ciks = safe("SEC 公司代號對照", us_data.ticker_ciks, {}, "https://www.sec.gov")
    us_fund: dict[str, dict] = {}
    okf = 0
    for tkr in [s for s in stocks if meta[s]["market"] == "US"]:
        cik = ciks.get(tkr.replace("-", "")) or ciks.get(tkr)
        if not cik:
            continue
        try:
            us_fund[tkr] = us_data.fundamentals(cik, float(frames[tkr]["close"].iloc[-1]))
            okf += bool(us_fund[tkr])
        except Exception as e:  # noqa: BLE001
            log(f"  fundamentals {tkr}: {e}")
    SOURCES.append({"name": "SEC XBRL 美股財報 (EPS/營收)", "ok": okf > 0 or not ciks, "url": "https://data.sec.gov",
                    "note": f"{okf} 檔取得"})

    # ---- 6. podcasts + GitHub tools ----------------------------------------
    names: dict[str, list[str]] = {}
    for s in stocks:
        m = meta[s]
        # Two-character Chinese names and word-like tickers (NOW, ARM, COST...) collide with
        # ordinary text, so those are matched only through the explicit aliases in the config.
        if m["market"] == "TW":
            base = [m["name"]] if len(m["name"]) >= 3 else []
        else:
            base = [m["code"]] if len(m["code"]) >= 3 and m["code"] not in AMBIGUOUS_TICKERS else []
        names[s] = list(dict.fromkeys(base + CFG["aliases"].get(m["code"], [])))
    for s, m in meta.items():  # ETFs are talked about by code (0050, QQQ) more than by name
        if m["kind"] == "etf":
            names[s] = [m["code"]] + ([m["name"]] if m["market"] == "TW" and len(m["name"]) >= 4 else [])
    matchers = media.build_matchers(names)
    feeds = CFG["podcasts"][:3] if QUICK else CFG["podcasts"]
    shows, mentions, perr = media.podcasts(feeds, matchers)
    SOURCES.append({"name": "Podcast RSS", "ok": bool(shows), "url": "",
                    "note": f"{len(shows)}/{len(feeds)} 個節目" + (f"；失敗：{'；'.join(perr)[:120]}" if perr else "")})
    if not shows:
        shows, mentions = prev_pod.get("shows", []), prev_pod.get("mentions", {})
    tools = safe("GitHub 開源投資工具", media.github_tools, [], "https://github.com/search") or \
        read_json(DATA / "tools.json", [])

    # ---- 7. scoring ---------------------------------------------------------
    tframes = {s: scoring.tech_frame(df) for s, df in frames.items()}
    # Last week's tactical read, to tell "just turned" from "has been strong for a while".
    week_key = "{}-W{:02d}".format(*dt.date.fromisoformat(trading_days[-1]).isocalendar()[:2])
    tactical_hist = read_json(DATA / "history" / "tactical.json", {})
    earlier = [k for k in sorted(tactical_hist) if k < week_key]
    tactical_prev = tactical_hist[earlier[-1]] if earlier else {}
    records = []
    for s in stocks:
        df, tf, m = frames[s], tframes[s], meta[s]
        row, code = tf.iloc[-1], m["code"]
        last = float(row["close"])
        rec: dict = {"symbol": s, "code": code, "name": m["name"], "market": m["market"],
                     "industry": m["industry"], "close": last, "date": df.index[-1].strftime("%Y-%m-%d"),
                     "chg1d": ret(df, 1), "chg5d": ret(df, 5), "r20": ret(df, 20), "r60": ret(df, 60),
                     "ytd": ytd(df), "rsi": row["rsi"], "ma20": row["ma20"], "ma60": row["ma60"],
                     "vol_ann": (row["vol"] or 0) * math.sqrt(252), "pos250": row["pos250"],
                     "mentions": mentions.get(s, 0)}
        notes = scoring.tech_reasons(row)
        if m["market"] == "TW":
            fl = flow_map.get(code)
            avgvol = float(df["volume"].tail(20).mean()) or None
            chips_in: dict = {"r5": rec["chg5d"]}
            if fl and avgvol:
                f = [x[0] for x in fl]
                t = [x[1] for x in fl]
                chips_in.update({"f5": sum(f[-5:]) / avgvol, "f20": sum(f) / avgvol, "t5": sum(t[-5:]) / avgvol,
                                 "streak": twse.streak(f)})
                rec["f5_adv"] = chips_in["f5"]
                rec.update({"foreign5": sum(f[-5:]) / 1000, "foreign20": sum(f) / 1000,
                            "trust5": sum(t[-5:]) / 1000, "foreign_streak": chips_in["streak"],
                            "foreign5_value": sum(f[-5:]) * last / 1e8})
            mg, mg0 = margin_now.get(code, {}).get("margin"), margin_ref.get(code, {}).get("margin")
            if mg is not None:
                rec["margin"] = mg
                rec["short"] = margin_now[code].get("short")
                if mg0 and mg0 >= 200:
                    chips_in["margin_chg5"] = rec["margin_chg5"] = mg / mg0 - 1
            chips, cn = scoring.chips_tw(chips_in)
            fb = first_by_code.get(code)
            if fb:  # shown as a reason; not part of the score (untested on this site's data)
                rec["trust_first"] = fb["streak"]
                cn.append((f"投信首度連買 {fb['streak']} 日（先前 {fb['quiet']} 日無買超）", 0.12))
            cost = (margin_cost.get(code) or [None])[0]
            me = leading.stock_maintenance(cost, last)
            if me and mg and mg * last * 1000 >= 1e8:
                rec.update({"margin_cost": cost, "maint_est": me})
                if me < 1.40:
                    cn.append((f"估算融資維持率 {me:.0%}，接近 130% 追繳線，留意斷頭賣壓", -0.12))
            fund_in = {**valuation.get(code, {}), **revenue.get(code, {}), **eps.get(code, {})}
            rm = leading.revenue_momentum({ym: t[code] for ym, t in rev_hist.items() if t.get(code)})
            if rm:
                fund_in.update({"rev_ratio": rm["ratio"], "rev_cross_up": rm["cross_up"],
                                "rev_cross_down": rm["cross_down"]})
                rec.update({"rev_ratio": rm["ratio"], "rev_cross": "up" if rm["cross_up"] else
                            "down" if rm["cross_down"] else None, "rev_rising": rm["rising"]})
            fund, fn = scoring.fund_tw(fund_in)
            # Financials report insurance contract liabilities under the same heading: a different thing.
            lg = None if "金融" in m["industry"] or "保險" in m["industry"] else leading.liability_growth(liab.get(code, {}))
            if lg:
                rec.update({"cl_qoq": lg["qoq"], "cl_yoy": lg["yoy"], "cl_share": lg["share"],
                            "cl_quarter": lg["quarter"], "cl_value": lg["value"] / 1e8})
                if (lg["share"] or 0) >= 3 and lg["qoq"] >= 0.15:
                    fn.append((f"合約負債季增 {lg['qoq']:+.0%}（占資產 {lg['share']:.0f}%），客戶預付款增加", 0.10))
                elif (lg["share"] or 0) >= 3 and lg["qoq"] <= -0.20:
                    fn.append((f"合約負債季減 {lg['qoq']:+.0%}，預收訂單消化中", -0.08))
            rec.update({k: fund_in.get(k) for k in ("pe", "pb", "yield", "rev_yoy", "rev_mom",
                                                    "rev_cum_yoy", "eps", "eps_period", "month")})
            ins = insiders.get(code)
            if ins:
                rec["insider_pledge"] = ins["pledge_ratio"]
                rec["insider_chg"] = ins["chg_since_elected"]
                if ins["pledge_ratio"] >= 0.3:
                    cn.append((f"董監質押比 {ins['pledge_ratio']:.0%} 偏高", -0.1))
        else:
            c = csum.get(code)
            chips_in = {"congress_net": (c["net"] if c else 0) if (congress_ok or trades) else None,
                        "guru_net": guru_net.get(code, 0) if gurus else None}
            chips, cn = scoring.chips_us(chips_in)
            rec.update({"congress_buys": c["buys"] if c else 0, "congress_sells": c["sells"] if c else 0,
                        "guru_net": guru_net.get(code, 0)})
            fund_in = us_fund.get(code, {})
            fund, fn = scoring.fund_us(fund_in)
            rec.update({k: fund_in.get(k) for k in ("pe", "eps", "eps_ttm", "eps_yoy", "rev_yoy", "eps_period")})
        buzz = scoring.buzz_score(rec["mentions"])
        if rec["mentions"]:
            notes.append((f"近兩週 {rec['mentions']} 集 Podcast 提及", 0.05))
        sc = scoring.score_stock(tf, chips, fund, buzz, slow_chips=m["market"] == "US")
        rec.update(sc)
        rec["setup"] = scoring.setup_tag(sc["tactical"], sc["structural"], tactical_prev.get(s))
        allnotes = notes + cn + fn
        rec["reasons"] = [t for t, w in sorted(allnotes, key=lambda x: -x[1]) if w > 0][:4]
        rec["risks"] = [t for t, w in sorted(allnotes, key=lambda x: x[1]) if w < 0][:3]
        rec["plan"] = scoring.trade_plan(row, sc["horizons"]["1m"], sc["overall"])
        rec["bars"] = len(df)
        records.append(rec)

    # ---- 8. picks / movers / backtest --------------------------------------
    def pick(market: str, n: int) -> list[dict]:
        cap, used, out = CFG["picks"]["max_per_industry"], {}, []
        pool = sorted((r for r in records if r["market"] == market and r["bars"] >= 120),
                      key=lambda r: -r["overall"])
        for r in pool:
            if used.get(r["industry"], 0) >= cap:
                continue
            used[r["industry"]] = used.get(r["industry"], 0) + 1
            out.append(r)
            if len(out) >= n:
                break
        for r in pool:  # the industry cap must never leave the list short
            if len(out) >= n:
                break
            if r not in out:
                out.append(r)
        return out

    picks = pick("TW", CFG["picks"]["tw"]) + pick("US", CFG["picks"]["us"])
    picks.sort(key=lambda r: -r["overall"])
    pick_syms = {r["symbol"] for r in picks}
    short = lambda r: (r["horizons"]["1w"]["score"] + r["horizons"]["1m"]["score"]) / 2  # noqa: E731
    bullish = sorted(records, key=lambda r: -short(r))[:12]
    bearish = sorted(records, key=short)[:12]
    bt = scoring.backtest({s: tframes[s] for s in stocks}) if len(stocks) >= 25 else {}

    # ---- 8b. ETFs and podcast-hot names -------------------------------------
    etf_records = []
    for s, m in meta.items():
        if m["kind"] != "etf" or len(frames[s]) < 60:
            continue
        if newest[m["market"]] is not None and frames[s].index[-1] < newest[m["market"]] - pd.Timedelta(days=7):
            continue  # stale prices: leave it out rather than score it
        etf_records.append(etf_record(s, frames[s], tframes[s], m, flow_map, mentions.get(s, 0)))
    etf_records.sort(key=lambda r: -r["overall"])
    by_sym = {r["symbol"]: r for r in records + etf_records}
    tactical_hist[week_key] = {r["symbol"]: r["tactical"] for r in records if r.get("tactical") is not None}
    write_json(DATA / "history" / "tactical.json", {k: tactical_hist[k] for k in sorted(tactical_hist)[-8:]})
    # Mentions per ISO week, so "new this week" can be told from "still being talked about".
    mention_hist = read_json(DATA / "history" / "podcast_mentions.json", {})
    mention_hist[week_key] = dict(mentions)
    mention_hist = {k: mention_hist[k] for k in sorted(mention_hist)[-26:]}
    write_json(DATA / "history" / "podcast_mentions.json", mention_hist)
    past_weeks = [mention_hist[k] for k in sorted(mention_hist) if k < week_key][::-1]

    def talk_weeks(sym: str) -> int:
        n = 1
        for wk in past_weeks:
            if sym not in wk:
                break
            n += 1
        return n

    hot = []
    for s, n in sorted(mentions.items(), key=lambda kv: -kv[1]):
        r = by_sym.get(s)
        if not r:
            continue
        eps = [{"show": sh["name"], "title": e["title"], "date": e["date"], "link": e["link"],
                "summary": e.get("summary", "")}
               for sh in shows for e in sh["episodes"] if s in e.get("mentions", [])]
        hot.append({**{k: r.get(k) for k in ("symbol", "code", "name", "market", "industry", "close", "chg5d",
                                             "r20", "rsi", "overall", "label", "horizons", "reasons", "risks")},
                    "kind": r.get("kind", "stock"), "mentions": n, "episodes": eps[:6], "view": hot_view(r, n),
                    "weeks": talk_weeks(s),
                    "signal": leading.attention_signal(n, talk_weeks(s), r.get("f5_adv"), r.get("margin_chg5"))
                    if r["market"] == "TW" else None})

    # ---- 8c. mutual funds: provider figures + our own evaluation --------------
    fund_cfg = CFG.get("funds", {})
    fund_list = fund_cfg.get("list", [])[:4] if QUICK else fund_cfg.get("list", [])
    fund_records = []
    for fid, group in fund_list:
        try:
            rec, nav = funds.build_record(fid, group, funds.fetch_nav(fid), funds.fetch_performance(fid),
                                          fund_cfg.get("risk_free", {}))
        except Exception as e:  # noqa: BLE001 - one fund failing must not sink the rest
            log(f"  fund {fid}: {e}")
            continue
        if len(nav) < 60:
            continue
        sym = rec["symbol"]
        df = pd.DataFrame({"open": nav, "high": nav, "low": nav, "close": nav, "volume": 0.0})
        frames[sym] = df
        meta[sym] = {"name": rec["name"], "market": "FUND", "kind": "fund", "code": fid, "scored": False,
                     "industry": rec["industry"], "line": True}
        trend = scoring.score_stock(scoring.tech_frame(df), None, None, None)
        rec.update({"horizons": trend["horizons"], "trend": trend["label"], "trend_score": trend["overall"]})
        fund_records.append(rec)
    SOURCES.append({"name": "鉅亨網 基金淨值與同類排名", "ok": bool(fund_records) or not fund_list,
                    "url": "https://fund.cnyes.com", "note": f"{len(fund_records)}/{len(fund_list)} 檔取得"})
    if not fund_records:  # source down: keep last week's table
        fund_records = read_json(DATA / "funds.json", [])
    fund_records.sort(key=lambda r: -(r["score"] if r.get("score") is not None else -1))

    # ---- 8d. leading indicators and the record of past picks ------------------
    lead = build_leading(frames, tframes, stocks, meta, flows, quotes, today)
    cohorts = [c for c in read_json(DATA / "history" / "picks.json", []) if c["date"] != trading_days[-1]]
    cohorts.append({"date": trading_days[-1],
                    "picks": [{"symbol": r["symbol"], "name": r["name"], "market": r["market"], "close": r["close"]}
                              for r in picks]})
    cohorts = cohorts[-60:]
    write_json(DATA / "history" / "picks.json", cohorts)
    track = leading.cohort_results(cohorts[:-1], frames, {"TW": "TWII", "US": "SPX"})

    # ---- 9. market outlook --------------------------------------------------
    def breadth(market: str) -> float | None:
        rs = [r for r in records if r["market"] == market and r.get("ma60")]
        return sum(r["close"] > r["ma60"] for r in rs) / len(rs) if rs else None

    fl_rows = [r for r in flows if "foreign" in r]
    tw_chips, tw_flow = None, {}
    if len(fl_rows) >= 5:
        f = [r["foreign"] for r in fl_rows]
        scale = (sum(abs(x) for x in f[-60:]) / len(f[-60:])) or 1.0
        f5, f20 = sum(f[-5:]), sum(f[-20:])
        tw_chips = 0.45 * math.tanh(f5 / (5 * scale) * 1.5) + 0.30 * math.tanh(f20 / (20 * scale) * 2)
        mrows = [r for r in flows if r.get("margin_bal")]
        m5 = (mrows[-1]["margin_bal"] / mrows[-6]["margin_bal"] - 1) if len(mrows) >= 6 else None
        m20 = (mrows[-1]["margin_bal"] / mrows[-21]["margin_bal"] - 1) if len(mrows) >= 21 else None
        idx5 = ret(twii, 5) or 0
        if m5 is not None and m5 > 0.02 and idx5 < -0.01:
            tw_chips -= 0.15
        b = breadth("TW")
        if b is not None:
            tw_chips += 0.5 * (b - 0.5)
        tw_flow = {"foreign_1d": f[-1], "foreign_5d": f5, "foreign_20d": f20,
                   "foreign_streak": twse.streak(f), "trust_5d": sum(r["trust"] for r in fl_rows[-5:]),
                   "dealer_5d": sum(r["dealer"] for r in fl_rows[-5:]),
                   "margin_bal": mrows[-1]["margin_bal"] if mrows else None, "margin_chg5": m5,
                   "margin_chg20": m20, "short_units": mrows[-1].get("short_units") if mrows else None,
                   "last_date": fl_rows[-1]["date"], "futures": futures, "breadth": b}
        mt = [r for r in flows if r.get("maint")]
        turnover = sum(q["value"] or 0 for q in quotes.values()) / 1e8
        if mt:
            last = mt[-1]
            tw_flow.update({
                "maint": last["maint"], "maint_date": last["date"], "maint_status": twse.maint_status(last["maint"]),
                "maint_chg5": last["maint"] - mt[-6]["maint"] if len(mt) >= 6 else None,
                "maint_low20": min(r["maint"] for r in mt[-20:]),
                # how far the collateral could fall before the market as a whole sits on the call line
                "maint_cushion": 1 - twse.MAINT_CALL / last["maint"], "collateral": last.get("collateral")})
        if mrows and mrows[-1].get("margin_units"):
            tw_flow["short_ratio"] = (mrows[-1].get("short_units") or 0) / mrows[-1]["margin_units"]
        if mrows and turnover:
            tw_flow.update({"turnover": turnover, "margin_days": mrows[-1]["margin_bal"] / turnover})
    vix = frames.get("VIX")
    us_chips = None
    if vix is not None:
        v, v5 = float(vix["close"].iloc[-1]), ret(vix, 5) or 0
        us_chips = (-0.25 if v >= 30 else -0.1 if v >= 22 else 0.1 if v < 15 else 0.0) + (-0.2 if v5 > 0.2 else 0)
        b = breadth("US")
        if b is not None:
            us_chips += 0.6 * (b - 0.5)

    indices_out = []
    for ix in CFG["indices"]:
        s = ix["symbol"]
        if s not in frames:
            continue
        df, tf = frames[s], tframes[s]
        row = tf.iloc[-1]
        item = {"symbol": s, "name": ix["name"], "market": ix["market"], "close": float(row["close"]),
                "date": df.index[-1].strftime("%Y-%m-%d"), "chg1d": ret(df, 1), "chg5d": ret(df, 5),
                "r20": ret(df, 20), "r60": ret(df, 60), "ytd": ytd(df), "rsi": row["rsi"],
                "ma20": row["ma20"], "ma60": row["ma60"],
                "spark": [round(float(x), 2) for x in df["close"].tail(60)]}
        if ix["market"] in ("TW", "US"):
            chips = tw_chips if ix["market"] == "TW" else us_chips
            item["horizons"] = {h: scoring.forecast(float(row["close"]), row["vol"], scoring.clamp(
                scoring.blend(h, {"tech": float(row[f"t_{h}"]), "chips": chips, "fund": None, "buzz": None})), d)
                for h, d in scoring.HORIZONS.items()}
            item["reasons"] = [t for t, _ in scoring.tech_reasons(row)]
        indices_out.append(item)

    narrative = build_narrative(indices_out, tw_flow, picks, bullish, bearish)
    brief = lambda r: {k: r.get(k) for k in ("code", "name", "market", "industry", "close", "chg5d", "r20",  # noqa: E731
                                             "overall", "label", "tactical", "structural", "setup",
                                             "reasons", "risks")}
    write_json(ROOT / ".cache" / "digest.json", {
        "data_date": trading_days[-1],
        "indices": [{k: i.get(k) for k in ("name", "close", "chg5d", "r20", "ytd", "rsi", "horizons")}
                    for i in indices_out],
        "tw_flow": tw_flow, "narrative": narrative, "backtest": bt,
        "leading": [{k: c.get(k) for k in ("name", "value_text", "change_text", "reading")} for c in lead["cards"]],
        "regime": lead.get("regime"),
        "options": [{k: o.get(k) for k in ("expiry", "call_wall", "put_wall", "max_pain")} for o in lead.get("options") or []],
        "trust_first": [{k: r.get(k) for k in ("code", "name", "streak", "quiet", "value")} for r in trust_first[:8]],
        "picks_track": track[-8:],
        "picks": [brief(r) for r in picks],
        "bullish": [brief(r) for r in bullish[:8]], "bearish": [brief(r) for r in bearish[:8]],
        "podcast_hot": [{**{k: h[k] for k in ("symbol", "name", "market", "industry", "close", "chg5d", "r20",
                                              "rsi", "overall", "label", "mentions", "reasons", "risks", "view")},
                         "episodes": [{k: e[k] for k in ("show", "title", "date", "summary")} for e in h["episodes"]]}
                        for h in hot[:12]],
        "etf": [{k: r.get(k) for k in ("symbol", "name", "market", "industry", "close", "chg5d", "r20", "r60", "ytd",
                                       "overall", "label", "foreign5")} for r in etf_records],
        "funds": [{k: r.get(k) for k in ("symbol", "name", "group", "industry", "currency", "ret", "bench", "beat",
                                         "excess_ann", "sharpe", "vol_3y", "mdd_3y", "score", "grade", "trend",
                                         "dist", "bench_name")} for r in fund_records]})

    # ---- 10. charts (also the on-disk price store for the next run) ----------
    if prices.Yahoo.enabled:  # optional Taiwan intraday for the names people will open first
        for s in ["TWII"] + [r["symbol"] for r in picks if r["market"] == "TW"]:
            intraday[s] = prices.Yahoo.intraday("^TWII" if s == "TWII" else s) or intraday.get(s, [])
    SOURCES.append({"name": "Yahoo Finance (選用：台股長期歷史/分時)", "ok": prices.Yahoo.enabled, "url":
                    "https://finance.yahoo.com", "note": "" if prices.Yahoo.enabled else "遭限流，本次略過"})
    for s, df in frames.items():
        _, old = load_store(s)
        payload = prices.compact(df, 5, old.get("w"))
        payload.update({"symbol": s, "name": meta[s]["name"], "i": intraday.get(s) or [],
                        "deep": bool(old.get("deep") or DEEP.get(s))})
        if meta[s].get("line"):
            payload["line"] = True  # NAV series: draw a line, not candles
        write_json(CHARTS / f"{fname(s)}.json", payload)

    # ---- 11. flows page payload --------------------------------------------
    def top_flow(idx: int, sign: int, n: int = 15) -> list[dict]:
        rows = []
        for code, series in flow_map.items():
            q = quotes.get(code)
            if not q or not q.get("close") or not twse.COMMON_STOCK.match(code):
                continue
            net5 = sum(x[idx] for x in series[-5:])
            rows.append({"code": code, "name": q["name"], "lots": net5 / 1000,
                         "value": net5 * q["close"] / 1e8, "streak": twse.streak([x[idx] for x in series])})
        return sorted(rows, key=lambda r: -sign * r["value"])[:n]

    margin_moves = []
    for code, mnow in margin_now.items():
        m0 = margin_ref.get(code, {}).get("margin")
        if not twse.COMMON_STOCK.match(code) or not m0 or m0 < 2000 or mnow["margin"] is None:
            continue
        q, c5 = quotes.get(code) or {}, closes_5.get(code)
        margin_moves.append({"code": code, "name": mnow["name"], "margin": mnow["margin"],
                             "chg": mnow["margin"] / m0 - 1, **margin_extra(mnow),
                             "price_chg5": q["close"] / c5 - 1 if q.get("close") and c5 else None})
    margin_moves.sort(key=lambda r: -r["chg"])
    # Forced-selling watch: margin positions whose estimated maintenance ratio is low, plus
    # heavily margined names that have already fallen hard this month.
    margin_risk = []
    for code, mnow in margin_now.items():
        close, old = spot_close.get(code), closes_20.get(code)
        if not twse.COMMON_STOCK.match(code) or not close or not mnow["margin"]:
            continue
        value = mnow["margin"] * 1000 * close / 1e8
        r20 = close / old - 1 if old else None
        extra = margin_extra(mnow)
        cost = (margin_cost.get(code) or [None])[0]
        me = leading.stock_maintenance(cost, close)
        fell = r20 is not None and r20 <= -0.15 and (extra["usage"] or 0) >= 0.10
        if value >= 3 and ((me is not None and me < 1.45) or fell):
            m0 = margin_ref.get(code, {}).get("margin")
            margin_risk.append({"code": code, "name": mnow["name"], "value": value, "r20": r20, **extra,
                                "cost": cost, "maint_est": me, "close": close,
                                "chg": mnow["margin"] / m0 - 1 if m0 else None})
    margin_risk.sort(key=lambda r: r["maint_est"] if r["maint_est"] is not None else 9)

    write_json(DATA / "flows.json", {
        "history": flows, "summary": tw_flow, "dates": flow_dates,
        "foreign_buy": top_flow(0, 1) if flow_map else [], "foreign_sell": top_flow(0, -1) if flow_map else [],
        "trust_buy": top_flow(1, 1) if flow_map else [], "trust_sell": top_flow(1, -1) if flow_map else [],
        "margin_up": margin_moves[:15], "margin_down": margin_moves[::-1][:15], "margin_ref_date": margin_ref_date,
        "margin_risk": margin_risk[:25], "sbl": lead.get("sbl"), "trust_first": trust_first[:20]})

    tw_ins = []
    for r in records:
        ins = insiders.get(r["code"]) if r["market"] == "TW" else None
        if ins:
            tw_ins.append({"code": r["code"], "name": r["name"], "held": ins["held"],
                           "chg": ins["chg_since_elected"], "pledge": ins["pledge_ratio"],
                           "top": ins["top"], "month": ins["month"]})
    if not insiders:  # source down this week: keep the previous table
        tw_ins = prev_smart.get("tw_insiders", [])
    for g in gurus:
        g.pop("actions", None)
    write_json(DATA / "smart_money.json", {
        "congress": {"trades": trades[:400], "live": congress_ok,
                     "by_ticker": sorted(({"ticker": k, **v} for k, v in csum.items()),
                                         key=lambda r: -(r["buys"] + r["sells"]))[:40]},
        "gurus": gurus, "tw_insiders": sorted(tw_ins, key=lambda r: -(r["pledge"] or 0))})
    write_json(DATA / "podcasts.json", {"shows": shows, "mentions": mentions, "hot": hot,
                                        "names": {s: meta[s]["name"] for s in mentions if s in meta}})
    write_json(DATA / "etf.json", etf_records)
    write_json(DATA / "funds.json", fund_records)
    write_json(DATA / "leading.json", {"cards": lead["cards"], "series": lead["series"],
                                       "options": lead.get("options"), "regime": lead.get("regime")})
    write_json(DATA / "tools.json", tools)
    slim = lambda r: {k: v for k, v in r.items() if k not in ("bars",)}  # noqa: E731
    write_json(DATA / "universe.json", [slim(r) for r in sorted(records, key=lambda r: -r["overall"])])
    write_json(DATA / "summary.json", {
        "generated_at": now.isoformat(timespec="seconds"), "data_date": trading_days[-1],
        "next_update": next_wednesday(today).isoformat(), "quick": QUICK,
        "indices": indices_out, "tw_flow": tw_flow, "narrative": narrative,
        "picks": [slim(r) for r in picks],
        "bullish": [slim(r) for r in bullish], "bearish": [slim(r) for r in bearish],
        "backtest": bt, "weights": scoring.WEIGHTS, "weights_us": scoring.WEIGHTS_SLOW_CHIPS,
        "sources": SOURCES, "picks_track": track,
        "counts": {"tw": sum(r["market"] == "TW" for r in records),
                   "us": sum(r["market"] == "US" for r in records), "etf": len(etf_records),
                   "fund": len(fund_records)}})
    bad = [s["name"] for s in SOURCES if not s["ok"]]
    log(f"done: {len(records)} stocks, {len(etf_records)} ETFs, {len(fund_records)} funds, {len(picks)} picks, "
        f"{len(hot)} podcast-hot, "
        f"failed sources: {bad or 'none'}")
    return 0


def update_revenue_history(revenue: dict) -> dict[str, dict]:
    """Monthly revenue per company for the last 15 months: {'YYYY-MM': {code: thousand TWD}}.

    The exchange's open data only has the latest month, so older months come once from
    the MOPS archive (one page per month) and are kept in history/tw_revenue.json.
    """
    path = DATA / "history" / "tw_revenue.json"
    hist: dict[str, dict] = read_json(path, {})
    for code, r in revenue.items():  # latest month straight from the open data
        if r.get("month") and r.get("revenue"):
            hist.setdefault(r["month"], {})[code] = r["revenue"]
    if not hist:
        raise RuntimeError("no monthly revenue to anchor the history")
    y, m = (int(x) for x in max(hist).split("-"))
    wanted = []
    for _ in range(3 if QUICK else 15):
        wanted.append(f"{y}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    for ym in wanted:
        if len(hist.get(ym, {})) >= 500:
            continue
        try:
            hist.setdefault(ym, {}).update(leading.mops_revenue(ym))
        except Exception as e:  # noqa: BLE001 - archive page missing or refused: try again next run
            log(f"  revenue {ym}: {e}")
    hist = {k: hist[k] for k in sorted(hist)[-15:]}
    write_json(path, hist)
    return hist


def update_contract_liabilities(codes: list[str], today: dt.date) -> dict[str, dict]:
    """Quarter-end contract liabilities per company, kept in history/tw_contract.json.

    A company is only asked for again once a quarter it should have filed is missing,
    so a normal week makes no requests at all.
    """
    path = DATA / "history" / "tw_contract.json"
    store: dict[str, dict] = read_json(path, {})
    want = leading.expected_quarter(today)
    start = dt.date(today.year - 2, today.month, 1).isoformat()
    todo = [c for c in codes if want not in store.get(c, {})]
    got = fails = 0
    missed: list[str] = []
    for round_no in range(2):  # the free tier refuses at random when busy: one more pass over the misses
        queue, missed = (todo if round_no == 0 else missed), []
        if round_no and queue:
            time.sleep(20)
        for code in queue:
            try:
                store[code] = {**store.get(code, {}), **leading.contract_liabilities(code, start)}
                got += 1
                fails = 0
            except Exception as e:  # noqa: BLE001
                fails += 1
                missed.append(code)
                if fails >= 10:  # really down or out of quota: keep what we have, try again next week
                    log(f"  contract liabilities: giving up after {fails} straight failures ({e})")
                    missed += queue[queue.index(code) + 1:]
                    break
        if fails >= 10:
            break
    write_json(path, {c: {q: store[c][q] for q in sorted(store[c])[-9:]} for c in sorted(store)})
    log(f"contract liabilities: {got}/{len(todo)} companies refreshed (quarter {want}), {len(missed)} left for next run")
    if not store:
        raise RuntimeError("no balance sheet could be fetched")
    return store


CARD_GROUP = {"adr": "資金與跨市場", "fx": "資金與跨市場", "real": "資金與跨市場", "curve": "資金與跨市場",
              "hy": "資金與跨市場", "semis": "資金與跨市場", "vixts": "資金與跨市場",
              "breadth_tw": "市場廣度", "breadth_us": "市場廣度", "ad": "市場廣度",
              "retail": "期貨、選擇權與放空", "top10": "期貨、選擇權與放空", "basis": "期貨、選擇權與放空",
              "pcr": "期貨、選擇權與放空", "optwall": "期貨、選擇權與放空", "sbl": "期貨、選擇權與放空"}


def _card(key: str, name: str, value_text: str, change_text: str, reading: str, tone: int = 0,
          spark=None) -> dict:
    return {"key": key, "name": name, "value_text": value_text, "change_text": change_text,
            "reading": reading, "tone": tone,
            "spark": [] if spark is None else [round(float(x), 4) for x in spark]}


def build_leading(frames: dict, tframes: dict, stocks: list[str], meta: dict, flows: list[dict],  # noqa: C901, PLR0915
                  quotes: dict, today: dt.date) -> dict:
    """Indicators that tend to move before prices do. Shown for context only: none of
    them feeds the scores, because this site has no history yet to test them on.
    Each indicator is built on its own, so one failing source costs one card."""
    cards, series, extra = [], {}, {"sbl": None, "options": None, "regime": None}
    pp = lambda x: f"{x * 100:+.1f} 個百分點"  # noqa: E731
    dated = lambda sr, n=250: [[d.strftime("%Y-%m-%d"), round(float(v), 4)] for d, v in sr.tail(n).items()]  # noqa: E731
    by_date = {r["date"]: r for r in flows}

    def adr():
        prem = leading.adr_premium(frames["TSM"]["close"], frames["2330.TW"]["close"], frames["USDTWD"]["close"])
        now, ago, pct = float(prem.iloc[-1]), float(prem.iloc[-21]), leading.percentile(prem)
        series["adr"] = dated(prem)
        cards.append(_card("adr", "台積電 ADR 溢價", f"{now:.1%}", f"20 日 {pp(now - ago)}",
                           f"高於過去一年 {pct:.0%} 的日子。溢價擴大代表海外買盤對台積電的出價高於台股，"
                           "常被視為外資回補的前兆；收斂則相反。", 1 if now > ago else -1, prem.tail(60)))

    def fx():
        c = frames["USDTWD"]["close"]
        r20, weak = float(c.iloc[-1] / c.iloc[-21] - 1), bool(c.iloc[-1] > c.tail(20).mean())
        cards.append(_card("fx", "美元／台幣", f"{c.iloc[-1]:.2f}", f"20 日 {r20:+.1%}",
                           ("台幣走貶、匯率在月線之上，外資匯出壓力仍在。" if weak else
                            "台幣走升、匯率跌破月線，通常伴隨外資匯入。")
                           + f"資料日 {c.index[-1]:%m/%d}（Fed 公布有數日落差）。", -1 if weak else 1, c.tail(60)))

    def real_yield():
        ry = prices.fred_daily("DFII10")["close"]
        chg = float(ry.iloc[-1] - ry.iloc[-21])
        cards.append(_card("real", "美國 10 年期實質殖利率", f"{ry.iloc[-1]:.2f}%", f"20 日 {chg:+.2f} 個百分點",
                           "實質利率上升會壓縮成長股評價，下降則有利科技股與新興市場資金流入。",
                           -1 if chg > 0.05 else 1 if chg < -0.05 else 0, ry.tail(60)))

    def vix_term():
        ts = leading.term_structure(frames["VIX"]["close"], prices.cboe_daily("_VIX3M")["close"])
        now = float(ts.iloc[-1])
        series["vix_term"] = dated(ts)
        cards.append(_card("vixts", "VIX 期限結構（VIX ÷ VIX3M）", f"{now:.2f}", f"5 日 {now - float(ts.iloc[-6]):+.2f}",
                           ("高於 1：短期恐慌高過三個月後的預期，屬於恐慌狀態；由高點回落常出現在急跌尾聲。"
                            if now >= 1 else "低於 1：屬正常狀態，短期避險需求不高。"), -1 if now >= 1 else 0, ts.tail(60)))

    def breadth_of(mk: str, label: str):
        fs = {s: tframes[s] for s in stocks if meta[s]["market"] == mk}
        b = leading.breadth(fs)
        a20, a60 = float(b["above20"].iloc[-1]), float(b["above60"].iloc[-1])
        series[f"breadth_{mk.lower()}"] = [[d.strftime("%Y-%m-%d"), round(float(x), 3), round(float(y), 3)]
                                           for d, x, y in b.itertuples()]
        read = ("不到 15% 的個股站上月線，屬於超賣區，常見於急跌尾聲。" if a20 < 0.15 else
                "超過 85% 的個股站上月線，短線過熱。" if a20 > 0.85 else
                "站上季線的個股不到四成，指數若續強須留意只靠少數權值股撐盤。" if a60 < 0.4 else
                "多數個股站上季線，漲勢有廣度支撐。" if a60 > 0.6 else "廣度中性。")
        cards.append(_card(f"breadth_{mk.lower()}", f"{label}廣度（觀察名單 {len(fs)} 檔）",
                           f"月線上 {a20:.0%}・季線上 {a60:.0%}", f"5 日 {pp(a20 - float(b['above20'].iloc[-6]))}",
                           read, 1 if a60 > 0.6 else -1 if a60 < 0.4 else 0, b["above20"].tail(60)))

    def retail():
        fp = leading.futures_positions()
        mtx, tmf = fp["mtx"], fp.get("tmf")
        if fp["date"] in by_date:
            by_date[fp["date"]].update({"retail_mtx": round(mtx["ratio"], 4),
                                        **({"retail_tmf": round(tmf["ratio"], 4)} if tmf else {})})
        r = mtx["ratio"]
        cards.append(_card("retail", "散戶小台多空比", f"{r:+.1%}",
                           (f"微台 {tmf['ratio']:+.1%}・" if tmf else "") + fp["date"][5:],
                           ("散戶明顯偏多。這是反向指標：散戶一面倒做多時，指數常有下跌壓力。" if r >= 0.2 else
                            "散戶明顯偏空。反向來看，容易出現軋空上漲。" if r <= -0.2 else
                            "散戶多空部位不極端，反向訊號不明顯。")
                           + "算法：小型臺指未平倉扣掉三大法人後的淨部位 ÷ 全市場未平倉。",
                           -1 if r >= 0.2 else 1 if r <= -0.2 else 0,
                           [x["retail_mtx"] for x in flows if x.get("retail_mtx") is not None][-60:]))

    def pcr():
        rows = leading.put_call()
        for x in rows:
            if x["date"] in by_date:
                by_date[x["date"]]["pcr_oi"] = round(x["pcr_oi"], 4)
        now = rows[-1]["pcr_oi"]
        cards.append(_card("pcr", "台指選擇權 Put/Call 未平倉比", f"{now:.0%}", f"5 日 {pp(now - rows[-6]['pcr_oi'])}",
                           ("高於 100%：賣權未平倉多於買權，一般解讀為下檔有支撐、偏多。" if now >= 1 else
                            "低於 100%：買權未平倉多於賣權，一般解讀為上檔有壓、偏空。"),
                           1 if now >= 1.1 else -1 if now < 0.9 else 0, [x["pcr_oi"] for x in rows]))

    def sbl():
        mdates = [r["date"] for r in flows if r.get("margin_bal")]
        if QUICK or len(mdates) < 6:
            return
        got = twse.fetch_days([mdates[-1], mdates[-6]], leading.sbl_table, "sbl", log)
        now_t, ref_t = got[mdates[-1]], got[mdates[-6]]
        closes = {c: q["close"] for c, q in quotes.items() if q.get("close")}
        total = sum(v["sbl"] * closes[c] for c, v in now_t.items() if c in closes) / 1e8
        before = sum(v["sbl"] * closes[c] for c, v in ref_t.items() if c in closes) / 1e8
        moves = leading.sbl_moves(now_t, ref_t, closes)
        chg = total / before - 1 if before else None
        extra["sbl"] = {"date": mdates[-1], "ref_date": mdates[-6], "total": total, "chg": chg,
                        "up": moves[:15], "down": moves[::-1][:15]}
        cards.append(_card("sbl", "借券賣出餘額（法人放空）", f"{total:,.0f} 億", f"5 日 {chg:+.1%}" if chg is not None else "",
                           "外資與法人放空走借券，散戶才用融券。借券賣出餘額增加代表法人放空或避險加重，"
                           "即使現股買賣超看起來中性。以最新收盤價計算市值。",
                           -1 if (chg or 0) > 0.03 else 1 if (chg or 0) < -0.03 else 0))

    def curve():
        yc = prices.fred_daily("T10Y2Y")["close"]
        now, chg = float(yc.iloc[-1]), float(yc.iloc[-1] - yc.iloc[-21])
        cards.append(_card("curve", "美債殖利率曲線（10 年 − 2 年）", f"{now:+.2f} 個百分點", f"20 日 {chg:+.2f}",
                           ("倒掛：短率高於長率，歷史上常領先景氣衰退一年左右。" if now < 0 else
                            "曲線為正斜率。" + ("近期快速變陡，若是因為短率下跌（市場押注降息），常出現在景氣轉弱初期。"
                                           if chg >= 0.25 else "變化不大，沒有明顯的景氣訊號。")),
                           -1 if now < 0 else 0, yc.tail(120)))

    def credit():
        oas = prices.fred_daily("BAMLH0A0HYM2")["close"]
        ratio = (frames["SOXX"]["close"] / frames["SPY"]["close"]).dropna()
        rg = leading.risk_regime(oas, ratio)
        extra["regime"] = rg
        series["hy_oas"] = dated(oas)
        series["semis_rel"] = dated(ratio)
        cards.append(_card("hy", "美國高收益債利差", f"{rg['oas']:.2f}%", f"10 日 {rg['oas_chg10']:+.2f} 個百分點",
                           ("利差偏高或快速擴大：信用市場開始要求更高的風險補償，股市下跌時不宜輕易當成買點。"
                            if rg["oas"] > 4.0 or rg["oas_chg10"] >= 0.5 else
                            "利差仍窄：信用市場平靜，股市回檔較可能是輪動或獲利了結，而不是資金面出問題。"
                            if rg["oas"] < 3.5 else "利差在中間地帶，尚未出現信用壓力。")
                           + f"資料日 {oas.index[-1]:%m/%d}。",
                           -1 if rg["oas"] > 4.0 or rg["oas_chg10"] >= 0.5 else 1 if rg["oas"] < 3.5 else 0, oas.tail(120)))
        gap = rg["ratio"] / rg["ratio_ma60"] - 1
        cards.append(_card("semis", "半導體相對強弱（SOXX ÷ SPY）", f"{rg['ratio']:.3f}", f"較 60 日均值 {gap:+.1%}",
                           ("半導體跑贏大盤且在季線之上，科技股仍是領漲族群。" if rg["semis_lead"] else
                            "半導體落後大盤、相對強度跌破季線，領漲地位鬆動；台股與半導體高度連動，須留意。"),
                           1 if rg["semis_lead"] else -1, ratio.tail(120)))

    def options():
        spot = float(frames["TWII"]["close"].iloc[-1])
        rows = http.get_json(f"{leading.TAIFEX}/DailyMarketReportOpt")
        d = next(r["Date"] for r in rows if r.get("Contract") == "TXO")
        data_date = dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
        walls = leading.option_walls(rows, max(today, data_date), spot)
        if not walls:
            raise RuntimeError("no live TXO expiry in the report")
        for w in walls:
            w["ladder"] = leading.trim_ladder(w["ladder"])
            w["date"] = data_date.isoformat()
            w["spot"] = spot
        extra["options"] = walls
        w = walls[0]
        cards.append(_card("optwall", f"台指選擇權未平倉區間（{w['expiry'][5:].replace('-', '/')} 結算）",
                           f"{w['put_wall']:,.0f} – {w['call_wall']:,.0f}", f"Max Pain {w['max_pain']:,.0f}",
                           f"賣權最大未平倉在 {w['put_wall']:,.0f}（一般視為下檔支撐），買權最大未平倉在 {w['call_wall']:,.0f}"
                           f"（上檔壓力）。加權指數 {spot:,.0f} "
                           + ("在區間內，結算前常被壓縮在兩者之間。" if w["put_wall"] <= spot <= w["call_wall"] else
                              "已在區間之外，賣方被迫避險時波動會放大。")
                           + f"未平倉為 {data_date:%m/%d} 資料。", 0))

    def top10():
        lt = leading.large_traders(http.get_json(f"{leading.TAIFEX}/OpenInterestOfLargeTradersFutures"))
        if lt["date"] in by_date:
            by_date[lt["date"]]["top10"] = round(lt["ratio"], 4)
        r = lt["ratio"]
        cards.append(_card("top10", "十大交易人台指期淨部位", f"{lt['net']:+,.0f} 口", f"占未平倉 {r:+.1%}・{lt['date'][5:]}",
                           ("前十大交易人（多為外資與大型法人）合計淨多單，" if r > 0 else "前十大交易人合計淨空單，")
                           + ("部位明顯偏多。" if r >= 0.1 else "部位明顯偏空，大戶在避險或看空。" if r <= -0.1 else "幅度不大。")
                           + "大戶部位通常比散戶有參考性，方向與散戶相反時尤其值得留意。",
                           1 if r >= 0.1 else -1 if r <= -0.1 else 0,
                           [x["top10"] for x in flows if x.get("top10") is not None][-60:]))

    def basis():
        fb = leading.futures_basis(http.get_json(f"{leading.TAIFEX}/DailyMarketReportFut"))
        spot = frames["TWII"]["close"].get(pd.Timestamp(fb["date"]))
        if spot is None:
            raise RuntimeError(f"no TAIEX close for {fb['date']}")
        diff = fb["settle"] - float(spot)
        if fb["date"] in by_date:
            by_date[fb["date"]]["basis"] = round(diff, 1)
        pct = diff / float(spot)
        cards.append(_card("basis", "台指期基差（期貨 − 現貨）", f"{diff:+,.0f} 點", f"{pct:+.2%}・{fb['date'][5:]}",
                           ("正價差：期貨比現貨貴，期貨市場對後市偏樂觀。" if diff > 0 else
                            "逆價差：期貨比現貨便宜，" + ("幅度大，期貨市場偏悲觀或避險需求高。" if pct <= -0.005 else "幅度不大。"))
                           + "6–9 月除權息旺季的逆價差屬正常，接近結算日基差會收斂到零。",
                           1 if pct >= 0.003 else -1 if pct <= -0.005 else 0,
                           [x["basis"] for x in flows if x.get("basis") is not None][-60:]))

    def ad():
        days = [r["date"] for r in flows][-25:]
        closes_of = lambda d: {c: bar[3] for c, bar in read_json(DAY_CACHE / f"{d}.json", {}).items()}  # noqa: E731
        for prev, d in zip(days, days[1:]):
            if "adv" in by_date[d] or not (DAY_CACHE / f"{d}.json").exists() or not (DAY_CACHE / f"{prev}.json").exists():
                continue
            res = leading.advance_decline(closes_of(d), closes_of(prev))
            if res:
                by_date[d].update({"adv": res[0], "dec": res[1]})
        rows = [r for r in flows if r.get("adv") is not None]
        if not rows:
            raise RuntimeError("no advance/decline data yet")
        last, ten = rows[-1], rows[-10:]
        ratio = sum(r["adv"] for r in ten) / max(1, sum(r["dec"] for r in ten))
        line, acc = [], 0
        for r in rows[-120:]:
            acc += r["adv"] - r["dec"]
            line.append(acc)
        twii = frames["TWII"]["close"]
        idx_up = len(twii) > len(ten) and float(twii.iloc[-1]) > float(twii.iloc[-len(ten) - 1])
        cards.append(_card("ad", "全市場漲跌家數（上市普通股）", f"漲 {last['adv']}・跌 {last['dec']}",
                           f"近 {len(ten)} 日漲跌比 {ratio:.2f}・{last['date'][5:]}",
                           ("指數上漲但下跌家數較多：漲勢集中在少數權值股，廣度沒有跟上。" if idx_up and ratio < 0.95 else
                            "指數下跌但上漲家數較多：賣壓集中在權值股，中小型股相對有撐。" if not idx_up and ratio > 1.05 else
                            "上漲家數明顯多於下跌，漲勢普遍。" if ratio >= 1.2 else
                            "下跌家數明顯多於上漲，賣壓普遍。" if ratio <= 0.8 else "漲跌家數接近，沒有明顯方向。"),
                           1 if ratio >= 1.2 else -1 if ratio <= 0.8 else 0, line))

    for name, fn, url in (("台積電 ADR 溢價", adr, ""), ("美元／台幣動能", fx, ""),
                          ("FRED 美國 10 年期實質殖利率", real_yield, "https://fred.stlouisfed.org"),
                          ("FRED 美債殖利率曲線", curve, "https://fred.stlouisfed.org"),
                          ("FRED 高收益債利差與半導體相對強弱", credit, "https://fred.stlouisfed.org"),
                          ("Cboe VIX3M 期限結構", vix_term, "https://www.cboe.com"),
                          ("台股廣度", lambda: breadth_of("TW", "台股"), ""), ("美股廣度", lambda: breadth_of("US", "美股"), ""),
                          ("全市場漲跌家數", ad, ""),
                          ("期交所 散戶小台／微台部位", retail, "https://openapi.taifex.com.tw"),
                          ("期交所 十大交易人部位", top10, "https://openapi.taifex.com.tw"),
                          ("期交所 台指期基差", basis, "https://openapi.taifex.com.tw"),
                          ("期交所 選擇權 Put/Call 比", pcr, "https://openapi.taifex.com.tw"),
                          ("期交所 選擇權未平倉分布", options, "https://openapi.taifex.com.tw"),
                          ("證交所 借券賣出餘額", sbl, "https://www.twse.com.tw")):
        safe(f"領先指標：{name}", fn, None, url)
    for c in cards:
        c["group"] = CARD_GROUP.get(c["key"], "其他")
    write_json(DATA / "history" / "tw_flows.json", flows)
    return {"cards": cards, "series": series, **extra}


def margin_extra(m: dict) -> dict:
    """Per-stock margin gauges that need no cost basis: quota used and short/margin ratio."""
    lots, limit, short = m.get("margin") or 0, m.get("limit") or 0, m.get("short") or 0
    return {"usage": lots / limit if limit else None, "short_ratio": short / lots if lots else None}


def etf_category(code: str, name: str) -> str:
    """Rough grouping of Taiwan ETFs from the code suffix and the fund name."""
    if code.endswith("L"):
        return "槓桿型"
    if code.endswith("R"):
        return "反向型"
    if code.endswith("B"):
        return "債券"
    if code.endswith("A") or "主動" in name:
        return "主動式"
    if any(k in name for k in ("高息", "高股息", "優息", "股利", "收益")):
        return "高股息"
    if any(k in name for k in ("半導體", "科技", "5G", "AI", "電動車", "資安", "生技", "金融")):
        return "產業主題"
    if any(k in name for k in ("50", "市值", "加權", "中型")):
        return "市值型"
    return "其他"


def etf_record(s: str, df: pd.DataFrame, tf: pd.DataFrame, m: dict, flow_map: dict, mentions: int) -> dict:
    """ETFs are scored on price behaviour (plus institutional flows in Taiwan); no fundamentals."""
    row = tf.iloc[-1]
    last = float(row["close"])
    rec: dict = {"symbol": s, "code": m["code"], "name": m["name"], "market": m["market"], "kind": "etf",
                 "industry": m["industry"], "close": last, "date": df.index[-1].strftime("%Y-%m-%d"),
                 "chg1d": ret(df, 1), "chg5d": ret(df, 5), "r20": ret(df, 20), "r60": ret(df, 60),
                 "r250": ret(df, 250), "ytd": ytd(df), "rsi": row["rsi"], "ma20": row["ma20"], "ma60": row["ma60"],
                 "vol_ann": (row["vol"] or 0) * math.sqrt(252), "pos250": row["pos250"],
                 "mdd": indicators.max_drawdown(df["close"], 250), "mentions": mentions,
                 "turnover": float((df["close"] * df["volume"]).tail(20).mean())}
    chips, notes = None, scoring.tech_reasons(row)
    fl = flow_map.get(m["code"]) if m["market"] == "TW" else None
    avgvol = float(df["volume"].tail(20).mean()) or None
    if fl and avgvol and any(x[0] or x[1] for x in fl):
        f, t = [x[0] for x in fl], [x[1] for x in fl]
        chips, cn = scoring.chips_tw({"f5": sum(f[-5:]) / avgvol, "f20": sum(f) / avgvol,
                                      "t5": sum(t[-5:]) / avgvol, "streak": twse.streak(f), "r5": rec["chg5d"]})
        notes += cn
        rec.update({"foreign5": sum(f[-5:]) / 1000, "trust5": sum(t[-5:]) / 1000, "foreign_streak": twse.streak(f)})
    if m["industry"] in ("槓桿型", "反向型"):
        notes.append(("槓桿／反向 ETF 每日重設有耗損，只適合短線", -0.2))
    rec.update(scoring.score_stock(tf, chips, None, scoring.buzz_score(mentions)))
    rec["reasons"] = [t for t, w in sorted(notes, key=lambda x: -x[1]) if w > 0][:4]
    rec["risks"] = [t for t, w in sorted(notes, key=lambda x: x[1]) if w < 0][:3]
    rec["plan"] = scoring.trade_plan(row, rec["horizons"]["1m"], rec["overall"])
    return rec


def hot_view(r: dict, mentions: int) -> str:
    """One-line reading of a podcast-hot name: does the attention agree with the data?"""
    o, rsi, wk = r["overall"], r.get("rsi") or 50, r.get("chg5d") or 0
    if o >= 0.15:
        txt = "聲量與數據同向偏多"
        txt += "，但 RSI 過熱，話題性可能已反映在股價，追高風險大" if rsi >= 72 else "，趨勢與籌碼支持，拉回可留意"
    elif o <= -0.15:
        txt = "討論度高但數據偏空"
        txt += "，屬於超跌後的關注，等止穩訊號再說" if rsi <= 32 else "，留意「利多不漲」，反彈宜減碼"
    else:
        txt = "市場關注度高、多空訊號混雜，方向未明，觀望為主"
    if abs(wk) >= 0.08:
        txt += f"（近一週已{'漲' if wk > 0 else '跌'} {abs(wk):.0%}，波動放大）"
    return txt


def next_wednesday(d: dt.date) -> dt.date:
    return d + dt.timedelta(days=(2 - d.weekday()) % 7 or 7)


def build_narrative(indices: list[dict], fl: dict, picks: list[dict], bull: list[dict],
                    bear: list[dict]) -> list[dict]:
    """Rule-based weekly reading - always available, no LLM involved."""
    by = {i["symbol"]: i for i in indices}
    out = []

    def trend_line(i: dict) -> str:
        pos = "站上" if i["close"] > (i.get("ma60") or 0) else "跌破"
        return (f"{i['name']}收 {i['close']:,.0f}，近一週 {i['chg5d']:+.1%}、近一月 {i['r20']:+.1%}，"
                f"{pos}季線，RSI {i['rsi']:.0f}")

    tw, sp, ndq, sox, vix = (by.get(k) for k in ("TWII", "SPX", "NDQ", "SOXX", "VIX"))
    lines = [trend_line(i) + "。" for i in (tw, sp, sox) if i]
    if vix:
        mood = "市場恐慌升溫" if vix["close"] >= 25 else "情緒偏樂觀" if vix["close"] < 15 else "波動處於中性區間"
        lines.append(f"VIX {vix['close']:.1f}，{mood}。")
    out.append({"title": "當下趨勢", "text": "".join(lines)})

    if fl:
        s = fl["foreign_streak"]
        txt = (f"外資最新一日{'買超' if fl['foreign_1d'] > 0 else '賣超'} {abs(fl['foreign_1d']):,.0f} 億元，"
               f"近5日合計 {fl['foreign_5d']:+,.0f} 億、近20日 {fl['foreign_20d']:+,.0f} 億"
               + (f"，已連續{abs(s)}日{'買超' if s > 0 else '賣超'}" if abs(s) >= 2 else "") + "。"
               f"投信近5日 {fl['trust_5d']:+,.0f} 億。")
        if fl.get("margin_bal"):
            txt += f"融資餘額 {fl['margin_bal']:,.0f} 億元"
            if fl.get("margin_chg5") is not None:
                txt += f"，一週 {fl['margin_chg5']:+.1%}"
            if fl.get("margin_chg20") is not None:
                txt += f"、一月 {fl['margin_chg20']:+.1%}"
            txt += "。"
            if fl.get("maint"):
                txt += (f"大盤融資維持率估算約 {fl['maint']:.0%}（{fl['maint_status']}"
                        + (f"，一週 {fl['maint_chg5'] * 100:+.0f} 個百分點" if fl.get("maint_chg5") is not None else "")
                        + f"），整體擔保品再跌約 {fl['maint_cushion']:.0%} 會碰到 130% 追繳線。")
                if fl["maint"] < 1.40:
                    txt += "維持率已低，續跌容易引發追繳與斷頭賣壓。"
            if (fl.get("margin_chg5") or 0) > 0.02 and fl["foreign_5d"] < 0:
                txt += "外資賣、融資增，籌碼由法人流向散戶，須提防多殺多。"
            elif (fl.get("margin_chg5") or 0) < -0.02 and fl["foreign_5d"] > 0:
                txt += "融資減、外資買，籌碼趨於安定。"
        fut = fl.get("futures")
        if fut and fut.get("net_oi") is not None:
            txt += f"外資台指期淨未平倉 {fut['net_oi']:+,.0f} 口（{'偏多' if fut['net_oi'] > 0 else '偏空避險'}）。"
        if fl.get("breadth") is not None:
            txt += f"觀察名單中 {fl['breadth']:.0%} 的個股站上季線。"
        out.append({"title": "籌碼面", "text": txt})

    hz = {"1w": "未來一週", "1m": "未來一月", "1q": "未來一季"}
    for i in (tw, sp):
        if not i or "horizons" not in i:
            continue
        parts = [f"{hz[h]}{f['label']}（上漲機率約 {f['p_up']:.0%}，區間 {f['low']:,.0f}–{f['high']:,.0f}）"
                 for h, f in i["horizons"].items()]
        out.append({"title": f"{i['name']}展望", "text": "；".join(parts) + "。"})

    avg = sum(r["overall"] for r in picks) / len(picks) if picks else 0
    tw_s = tw["horizons"]["1m"]["score"] if tw and "horizons" in tw else 0
    if tw_s >= 0.15:
        adv = "大盤中期偏多，可維持六至八成持股，以精選名單中趨勢與籌碼同步的個股為主，沿月線操作。"
    elif tw_s > -0.15:
        adv = "大盤方向不明，建議持股五成上下，汰弱留強，新倉以拉回支撐不破者為限。"
    else:
        adv = "大盤中期偏空，建議降低持股至三至五成，反彈減碼弱勢股，保留現金等待籌碼回穩。"
    if bull and bear:
        adv += (f"短線相對強勢：{'、'.join(r['name'] for r in bull[:4])}；"
                f"相對弱勢須留意：{'、'.join(r['name'] for r in bear[:4])}。")
    adv += f"精選 20 檔平均綜合分數 {avg:+.2f}。所有價位皆為參考，請務必設定停損。"
    out.append({"title": "操作建議", "text": adv})
    return out


if __name__ == "__main__":
    sys.exit(main())
