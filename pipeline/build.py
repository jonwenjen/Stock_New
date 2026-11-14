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

import numpy as np
import pandas as pd

from . import funds, http, indicators, leading, market, media, prices, research, scoring, tpex, twse, us_data

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


def shard_put(folder: str, rows: dict[str, dict]) -> None:
    """Add days to monthly archive shards, history/<folder>/YYYY-MM.json = {"codes": [...], "rows":
    {date: [value per code]}}. Days already archived are left alone and only shards that gain a day
    are rewritten, so the weekly commit stays small however long the archive grows."""
    by_month: dict[str, dict] = {}
    for d, row in rows.items():
        by_month.setdefault(d[:7], {})[d] = row
    for month, days in by_month.items():
        path = DATA / "history" / folder / f"{month}.json"
        sh = read_json(path, {"codes": [], "rows": {}})
        new = sorted(d for d in days if d not in sh["rows"])
        if not new:
            continue
        pos = {c: i for i, c in enumerate(sh["codes"])}
        for d in new:
            for c in days[d]:
                if c not in pos:
                    pos[c] = len(sh["codes"])
                    sh["codes"].append(c)
            sh["rows"][d] = [days[d].get(c) for c in sh["codes"]]
        write_json(path, sh)


def shard_get(folder: str) -> dict[str, dict]:
    """Read a monthly archive back as {date: {code: value}}."""
    out: dict[str, dict] = {}
    for path in sorted((DATA / "history" / folder).glob("*.json")):
        sh = read_json(path, {})
        codes = sh.get("codes") or []
        for d, row in (sh.get("rows") or {}).items():
            out[d] = {c: v for c, v in zip(codes, row) if v is not None}
    return out


def shard_days(folder: str) -> set[str]:
    return {d for path in (DATA / "history" / folder).glob("*.json") for d in (read_json(path, {}).get("rows") or {})}


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
    shard_put("stock_flows_tw", store)  # kept for good: validating the chips weight needs a long history
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


CLEAN_LOG: dict[str, list[str]] = {}  # symbol -> what sanitising removed or rescaled this run


def clean(sym: str, df: pd.DataFrame, market: str, **kw) -> pd.DataFrame:
    """prices.sanitize with the notes kept for the quality report."""
    out, applied, notes, total = prices.sanitize(df, market=market, **kw)
    if notes:
        CLEAN_LOG[sym] = CLEAN_LOG.get(sym, []) + notes
    clean.last = (applied, total)
    return out


def fetch_index(ix: dict, intraday: dict) -> pd.DataFrame:
    df = _fetch_index(ix, intraday)
    market = {"BTC": "CRYPTO"}.get(ix["symbol"], "US" if ix["source"] == "cboe" else ix["source"].upper())
    return clean(ix["symbol"], df, market)


def _fetch_index(ix: dict, intraday: dict) -> pd.DataFrame:
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


def update_derivs(today: dt.date, twii: pd.Series) -> dict[str, dict]:
    """Daily derivatives positioning kept in history/tw_derivs.json, from TAIFEX's dated CSVs.

    The OpenAPI only serves the latest day, so the first run back-fills twelve months (one
    window of six requests per month, newest first); later runs refresh the last ~40 days.
    """
    path = DATA / "history" / "tw_derivs.json"
    store = read_json(path, {})
    n_windows = 2 if len(store) >= 200 or QUICK else 13
    # Two of the downloads return nothing when the end date is past the latest published day.
    end = min(today, twii.index[-1].date()) if len(twii) else today
    for i in range(n_windows):
        start = end - dt.timedelta(days=30 if i else 20)
        try:
            for d, v in leading.derivatives_window(start.isoformat(), end.isoformat()).items():
                store.setdefault(d, {}).update(v)
        except Exception as e:  # noqa: BLE001 - a missing month only shortens the history
            log(f"  derivatives {start}..{end}: {str(e)[:100]}")
        end = start - dt.timedelta(days=1)
    for d, v in store.items():
        c = twii.get(pd.Timestamp(d))
        if v.get("settle") and c is not None and not pd.isna(c):
            v["basis"] = round(v["settle"] - float(c), 1)
    store = {d: store[d] for d in sorted(store)[-520:]}
    write_json(path, store)
    if not store:
        raise RuntimeError("no derivatives history")
    return store


def update_qfii(trading_days: list[str], codes: set[str]) -> dict[str, dict]:
    """Foreign holding ratio per listed stock today and 20 sessions ago (two requests; cached
    locally), archived weekly in history/tw_qfii.json. {code: {"pct", "room", "chg20"}}."""
    days = [trading_days[-1], trading_days[-21]] if len(trading_days) > 21 else trading_days[-1:]

    def get(d: str) -> dict:
        path = ROOT / ".cache" / "twse_qfii" / f"{d}.json"
        if path.exists():
            return read_json(path, {})
        data = twse.foreign_holding(d)
        write_json(path, data)
        return data

    got = twse.fetch_days(days, get, "foreign holding", log)
    now = got.get(days[0])
    if not now:
        raise RuntimeError("no foreign holding table")
    ago = got.get(days[-1], {}) if len(days) > 1 else {}
    hist = read_json(DATA / "history" / "tw_qfii.json", {})
    hist[days[0]] = {c: v["pct"] for c, v in now.items() if c in codes and v.get("pct") is not None}
    write_json(DATA / "history" / "tw_qfii.json", {d: hist[d] for d in sorted(hist)[-60:]})
    out = {}
    for c, v in now.items():
        if v.get("pct") is None:
            continue
        a = (ago.get(c) or {}).get("pct")
        out[c] = {"pct": v["pct"], "room": v.get("room"), "chg20": round(v["pct"] - a, 2) if a is not None else None}
    return out


def update_form4(tickers: list[str], ciks: dict, today: dt.date) -> dict:
    """US insiders' open-market trades from SEC Form 4, kept in history/us_form4.json for 180 days.
    Each filing is downloaded once; later runs only fetch filings they have not seen."""
    path = DATA / "history" / "us_form4.json"
    store = read_json(path, {"seen": {}, "trades": []})
    since = (today - dt.timedelta(days=120 if not store["seen"] else 21)).isoformat()
    seen, ok = set(store["seen"]), 0
    for tkr in (tickers[:5] if QUICK else tickers):
        cik = ciks.get(tkr.replace("-", "")) or ciks.get(tkr)
        if not cik:
            continue
        try:
            got = us_data.recent_form4(int(cik), since, seen)
        except Exception as e:  # noqa: BLE001
            log(f"  form4 {tkr}: {str(e)[:80]}")
            continue
        ok += 1
        for acc, trades in got.items():
            store["seen"][acc] = today.isoformat()
            store["trades"] += [{**t, "ticker": tkr} for t in trades]
    cut = (today - dt.timedelta(days=180)).isoformat()
    store["trades"] = sorted((t for t in store["trades"] if t["date"] >= cut), key=lambda t: t["date"], reverse=True)
    store["seen"] = {a: d for a, d in store["seen"].items() if d >= cut}
    write_json(path, store)
    log(f"form 4: {ok} companies checked, {len(store['trades'])} trades on file")
    if tickers and not ok:
        raise RuntimeError("no company could be checked")
    return store


def update_tdcc(codes: set[str]) -> dict[str, dict]:
    """Weekly TDCC shareholder distribution, archived in history/tdcc.json (the source only
    ever offers the latest week, so history exists only from the first week it was saved).
    Returns {code: {"big1000", "big400", "holders", "chg1000", "chg_holders", "date"}}."""
    path = DATA / "history" / "tdcc.json"
    hist = read_json(path, {})
    date, now = leading.tdcc_distribution(codes)
    if not date or not now:
        raise RuntimeError("empty TDCC table")
    hist[date] = now
    hist = {d: hist[d] for d in sorted(hist)[-60:]}
    write_json(path, hist)
    prev = hist[sorted(hist)[-2]] if len(hist) >= 2 else {}
    out = {}
    for code, (b1000, b400, holders) in now.items():
        p = prev.get(code)
        out[code] = {"big1000": b1000, "big400": b400, "holders": holders, "date": date,
                     "chg1000": round(b1000 - p[0], 2) if p else None,
                     "chg_holders": (holders / p[2] - 1) if p and p[2] else None}
    return out


def build_calendar(data_date: str, flags: dict, meta: dict) -> list[dict]:
    """What is scheduled in the two weeks after the data date that affects prices or the
    numbers on this site: ex-dividend dates of tracked Taiwan names, disposition periods
    ending, index-option settlements and filing deadlines."""
    d0 = dt.date.fromisoformat(data_date)
    end = d0 + dt.timedelta(days=14)
    names = {m["code"]: (s, m["name"]) for s, m in meta.items() if m["market"] == "TW" and m.get("code")}
    out = []
    for code, fl in flags.items():
        if code not in names:
            continue
        sym, name = names[code]
        ex = fl.get("exdiv")
        if ex and d0 < dt.date.fromisoformat(ex["date"]) <= end:
            cash = f"，現金股利 {ex['cash']:g} 元" if ex.get("cash") else ""
            out.append({"date": ex["date"], "kind": "除權息", "symbol": sym,
                        "text": f"{name}（{code}）除{ex['kind'] or '息'}{cash}：當天股價會扣掉股利，不是下跌"})
        pu = fl.get("punish")
        if pu and pu.get("to") and d0 <= dt.date.fromisoformat(pu["to"]) <= end:
            out.append({"date": pu["to"], "kind": "處置", "symbol": sym,
                        "text": f"{name}（{code}）{pu['measure']}至此日，期間分盤撮合、須預收款券"})
    day = d0 + dt.timedelta(days=1)
    while day <= end:
        if day.weekday() == 2:
            third = 15 <= day.day <= 21
            out.append({"date": day.isoformat(), "kind": "結算",
                        "text": "台指選擇權月選與台指期結算日" if third else "台指選擇權週選結算日"})
        if day.day == 10:
            out.append({"date": day.isoformat(), "kind": "營收", "text": "上市櫃公司上月營收公布截止日"})
        if (day.month, day.day) in ((3, 31), (5, 15), (8, 14), (11, 14)):
            out.append({"date": day.isoformat(), "kind": "財報", "text": "上市櫃公司季報（或年報）公布截止日"})
        # Index rebalances move a lot of passive money on known dates (review 2026-11-14, chips-7). The
        # dates follow each index provider's published rule; holidays can shift them, so they are marked.
        last_weekday = day.weekday() < 5 and all((day + dt.timedelta(days=k)).month != day.month or
                                                 (day + dt.timedelta(days=k)).weekday() >= 5 for k in range(1, 4))
        if day.month in (2, 5, 8, 11) and last_weekday:
            out.append({"date": day.isoformat(), "kind": "指數調整",
                        "text": "MSCI 季度調整生效日（依規則推算，以公告為準）：被動資金在收盤換股，成分股尾盤量價常異常"})
        if day.month in (3, 6, 9, 12) and day.weekday() == 4 and 15 <= day.day <= 21:
            out.append({"date": day.isoformat(), "kind": "指數調整",
                        "text": "臺灣 50 指數（0050）季度成分股調整，收盤後生效（依規則推算，以公告為準）"})
        if day.month in (3, 6, 9, 12) and last_weekday:
            out.append({"date": day.isoformat(), "kind": "季底", "text": "季底：投信作帳與法人結帳的最後一天"})
        day += dt.timedelta(days=1)
    return sorted(out, key=lambda x: (x["date"], x["kind"]))


# ----------------------------- over-the-counter (TPEx) ----------------------------- #
OTC_CACHE = ROOT / ".cache" / "tpex_days"


def tpex_days(dates: list[str], fn, label: str) -> dict:
    """twse.fetch_days for TPEx requests: same back-off, but a TPEx refusal must not stop TWSE requests."""
    saved = twse.THROTTLED
    twse.THROTTLED = False
    try:
        return twse.fetch_days(dates, fn, label, log, cooldown=45.0)
    finally:
        twse.THROTTLED = saved


def cached_otc_day(d: str) -> dict:
    path = OTC_CACHE / f"{d}.json"
    if path.exists():
        return read_json(path, {})
    data = tpex.day_quotes(d)
    write_json(path, data)
    return data


def otc_latest_day(trading_days: list[str]) -> dict:
    """The newest OTC quote table available (today's may not be published yet)."""
    got = tpex_days(trading_days[-1:], cached_otc_day, "OTC quotes")
    if not got:
        got = tpex_days(trading_days[-2:-1], cached_otc_day, "OTC quotes")
    if not got:
        raise RuntimeError("no OTC quote table")
    return next(iter(got.values()))


def update_otc_prices(trading_days: list[str], codes: list[str], actions: dict) -> dict[str, pd.DataFrame]:
    """OTC price store in charts/<code>_TWO.json, built from TPEx's daily all-securities tables.

    One request covers every OTC stock for a day; days are fetched newest first, at most 60
    per run after the first (a new store needs about 260 sessions for the 52-week high).
    """
    cal = pd.DatetimeIndex(pd.to_datetime(trading_days))
    stored = {c: load_store(f"{c}.TWO") for c in codes}
    have = {d for c, (f, _) in stored.items() for d in f.index.strftime("%Y-%m-%d")}
    window = trading_days[-(30 if QUICK else 260):]
    cover = {d: sum(d in set(f.index.strftime("%Y-%m-%d")) for f, _ in stored.values()) for d in window} if have else {}
    need = [d for d in window if cover.get(d, 0) < 0.8 * len(codes)]
    cached = [d for d in need if (OTC_CACHE / f"{d}.json").exists()]
    todo = [d for d in reversed(need) if d not in cached][:260 if not have else 60]
    tables = {d: read_json(OTC_CACHE / f"{d}.json", {}) for d in cached}
    tables.update(tpex_days(todo, cached_otc_day, "OTC quotes"))
    out = {}
    for code in codes:
        old, prev = stored[code]
        rows = [(d, *t[code]) for d, t in sorted(tables.items()) if code in t]
        new = pd.DataFrame([r[1:] for r in rows], columns=prices.COLS, index=pd.to_datetime([r[0] for r in rows])) if rows else None
        applied = prev.get("applied") or []
        if new is not None and len(old):
            new = new[~new.index.isin(old.index)]
            if applied and len(new):
                k = pd.Series([fill_scale(applied, t) for t in new.index], index=new.index)
                new[["open", "high", "low", "close"]] = new[["open", "high", "low", "close"]].mul(k, axis=0)
        df = prices.merge(new, old if len(old) else None)
        if df.empty:
            continue
        df = clean(f"{code}.TWO", df, "TW", code=code, calendar=cal, events=actions.get(code), applied=applied)
        applied, total = clean.last
        payload = prices.compact(df, 5, prices.scale_rows(prev.get("w") or [], total))
        payload.update({"symbol": f"{code}.TWO", "name": prev.get("name") or code, "i": [], "applied": applied})
        write_json(CHARTS / f"{code}_TWO.json", payload)
        if len(df) >= 60:
            out[code] = df
    log(f"OTC prices: {len(out)}/{len(codes)} stocks with 60+ sessions ({len(todo)} days requested)")
    if codes and not out:
        raise RuntimeError("no OTC price history yet")
    return out


def otc_stock_flows(trading_days: list[str]) -> tuple[list[str], dict[str, list[tuple]]]:
    """Per-stock OTC net shares (foreign, trust) for the latest N sessions, kept in
    history/tpex_stock_flows.json (in lots) like the listed-stock version."""
    path = DATA / "history" / "tpex_stock_flows.json"
    store: dict[str, dict] = read_json(path, {})
    n = 5 if QUICK else CFG["stock_flow_days"]
    need = [d for d in trading_days[-n:] if d not in store]
    for d, table in tpex_days(need, tpex.inst_stocks, "OTC institutional").items():
        store[d] = {c: [round(v[0] / 1000), round(v[1] / 1000)] for c, v in table.items()
                    if tpex.COMMON_STOCK.match(c) and (v[0] or v[1])}
    shard_put("stock_flows_otc", store)
    store = {d: store[d] for d in sorted(store)[-(n + 10):]}
    write_json(path, store)
    dates = sorted(store)[-n:]
    if not dates:
        raise RuntimeError("no OTC institutional data")
    codes = set().union(*(set(store[d]) for d in dates))
    return dates, {c: [tuple(x * 1000 for x in store[d].get(c, (0, 0))) for d in dates] for c in codes}


def update_tw_actions(today: dt.date, backfill: int = 0, current: bool = True) -> dict[str, dict[str, float]]:
    """Ex-rights / ex-dividend factors per stock, kept in history/tw_actions.json.

    Every run asks for the current half-year window (recent events must be applied before
    prices are scored). `backfill` older windows, newest first, are added on top: the table is
    heavy and TWSE throttles it, so five years are filled in a couple of windows per week.
    """
    path = DATA / "history" / "tw_actions.json"
    store = read_json(path, {"done": [], "events": {}})
    windows = []
    start = dt.date(today.year - 5, 1, 1)
    while start <= today:
        end = min(dt.date(start.year + (start.month > 6), 1 if start.month > 6 else 7, 1) - dt.timedelta(days=1), today)
        windows.append((start.isoformat(), end.isoformat()))
        start = end + dt.timedelta(days=1)
    done = set(store["done"])
    # a window is final once it ended more than a week ago; the current one is always refreshed
    recent = [w for w in windows if dt.date.fromisoformat(w[1]) >= today - dt.timedelta(days=30)]
    older = [w for w in windows if w not in recent and f"{w[0]}~{w[1]}" not in done][::-1][:backfill]
    todo = (recent[::-1] if current else []) + older
    got = twse.fetch_days([f"{a}~{b}" for a, b in todo], lambda key: twse.ex_rights(*key.split("~")), "ex-rights", log,
                          cooldown=60.0)
    for key, ev in got.items():
        for code, days in ev.items():
            store["events"].setdefault(code, {}).update(days)
        if dt.date.fromisoformat(key.split("~")[1]) < today - dt.timedelta(days=7):
            store["done"] = sorted(set(store["done"]) | {key})
    write_json(path, store)
    log(f"ex-rights: {len(got)}/{len(todo)} windows fetched, events for {len(store['events'])} codes")
    if todo and not got and not store["events"]:
        raise RuntimeError("no ex-rights data")
    return store["events"]


def backfill_tw_actions(today: dt.date) -> None:
    """End-of-run back-fill of older ex-rights windows. A refusal here must not mark TWSE as
    throttled for anything else, and nothing in this run depends on it (next week's does)."""
    saved = twse.THROTTLED
    twse.THROTTLED = False
    try:
        update_tw_actions(today, backfill=2, current=False)
    finally:
        twse.THROTTLED = saved


def industry_fallback(code: str) -> str:
    """Industry when the monthly-revenue table has none. Financial holding companies (28xx) file
    no monthly revenue, so they would otherwise land in "其他" and escape the per-industry cap."""
    return "金融保險業" if re.fullmatch(r"28\d\d", code) else "其他"


def fill_scale(applied: list[str], date: pd.Timestamp) -> float:
    """Factor that puts a raw bar from `date` on the same scale as an adjusted history."""
    f, seen = 1.0, set()
    for key in applied:
        d, x = key.split(":")
        if d not in seen and pd.Timestamp(d) > date:
            f *= float(x)
        seen.add(d)
    return f


def update_tw_prices(trading_days: list[str], universe: list[str], actions: dict | None = None) -> dict[str, pd.DataFrame]:
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

    cal = pd.DatetimeIndex(pd.to_datetime(trading_days))
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
    # Point-in-time universe: which listed stocks traded the most value each day. A back-test
    # that uses today's most active stocks for past dates flatters itself (survivorship); this
    # archive lets later tests use the list as it was. About 1 KB a day.
    pit_path = DATA / "history" / "tw_universe_pit.json"
    pit = read_json(pit_path, {})
    # Days fetched in earlier runs sit in the local cache: archive those too, not just today's fetches.
    # The closes of every listed common stock go to monthly shards (history/pit_closes), so a later
    # back-test can follow stocks that have since left the universe - no extra requests.
    cached = {f.stem: f for f in DAY_CACHE.glob("*.json")} if DAY_CACHE.exists() else {}
    have_closes, new_closes = shard_days("pit_closes"), {}
    for d in sorted(set(tables) | set(cached)):
        if d in pit and d in have_closes:
            continue
        table = tables.get(d) or read_json(cached[d], {})
        if not table:
            continue
        if d not in pit:
            vals = sorted(((c, bar[3] * bar[4]) for c, bar in table.items() if twse.COMMON_STOCK.match(c)),
                          key=lambda x: -x[1])
            pit[d] = [c for c, _ in vals[:150]]
        if d not in have_closes:
            new_closes[d] = {c: round(bar[3], 2) for c, bar in table.items() if twse.COMMON_STOCK.match(c) and bar[3]}
    write_json(pit_path, {d: pit[d] for d in sorted(pit)[-1300:]})
    shard_put("pit_closes", new_closes)
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
        old = stored.get(code)
        applied = load_store(f"{code}.TW")[1].get("applied") or []
        if new is not None and old is not None and len(old):
            # The stored history may already be adjusted for later corporate actions: bars it
            # holds win, and bars that only fill gaps are put on the same scale first.
            new = new[~new.index.isin(old.index)]
            if applied and len(new):
                k = pd.Series([fill_scale(applied, t) for t in new.index], index=new.index)
                new[["open", "high", "low", "close"]] = new[["open", "high", "low", "close"]].mul(k, axis=0)
        df = prices.merge(new, old) if old is not None else prices.merge(None, new)
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
            prev = load_store(f"{code}.TW")[1]
            df = clean(f"{code}.TW", df, "TW", code=code, calendar=cal, events=(actions or {}).get(code),
                       applied=prev.get("applied"))
            out[code] = (df, *clean.last)
    if not out:
        raise RuntimeError("no Taiwan price data")
    frames = {}
    for code, (df, applied, total) in out.items():  # checkpoint now: a later failure must not waste the download
        sym = f"{code}.TW"
        prev = load_store(sym)[1]
        payload = prices.compact(df, 5, prices.scale_rows(prev.get("w") or [], total))
        payload.update({"symbol": sym, "name": prev.get("name") or code, "i": prev.get("i") or [],
                        "deep": bool(prev.get("deep") or DEEP.get(sym)), "applied": applied})
        write_json(CHARTS / f"{fname(sym)}.json", payload)
        frames[code] = df
    return frames


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
    prev_all = read_json(DATA / "universe.json", [])
    prev_tw = {r["code"]: r for r in prev_all if r.get("market") == "TW" and not str(r.get("symbol", "")).endswith(".TWO")}
    prev_otc = {r["code"]: r for r in prev_all if str(r.get("symbol", "")).endswith(".TWO")}
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
    # A source can also be up but missing single companies (quarterly tables are rebuilt during
    # filing season): those keep last week's figure, which carries its own period, rather than going blank.
    for fresh, keys in ((valuation, ("pe", "pb", "yield")), (eps, ("eps", "eps_period"))):
        for c, old_vals in carry(keys).items():
            if c not in fresh and any(v is not None for v in old_vals.values()):
                fresh[c] = old_vals
    insiders = safe("證交所 董監持股", lambda: retry(twse.insiders), {}, tw_api)
    daytrade = safe("證交所 個股當日沖銷", lambda: twse.day_trading(trading_days[-1]), {}, "https://www.twse.com.tw")
    margins = safe("證交所 營益分析（毛利率、營益率）", lambda: retry(twse.margins), {}, tw_api)
    if margins:  # archived per period: quarter-on-quarter trends become possible from the next filing
        mpath = DATA / "history" / "tw_margins.json"
        mh = read_json(mpath, {})
        for c, v in margins.items():
            if v.get("period"):
                mh.setdefault(v["period"], {})[c] = [v["gm"], v["om"], v["nm"], v["rev_m"]]
        write_json(mpath, {k: mh[k] for k in sorted(mh)[-12:]})
    etf_nav = safe("證交所 ETF 淨值與折溢價", twse.etf_nav, {}, "https://mis.twse.com.tw")
    flags_path = DATA / "history" / "tw_flags.json"
    flags = safe("證交所 處置／注意股與除權息預告", lambda: retry(twse.trading_flags), None, tw_api)
    if flags:
        write_json(flags_path, {"fetched": today.isoformat(), "flags": flags})
    else:
        flags = twse.carry_flags(read_json(flags_path, {}).get("flags", {}), today.isoformat())
        log(f"  trading flags: feed unavailable, reusing {len(flags)} unexpired flags from the last run")
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


    # ---- 3. universes -------------------------------------------------------
    forced = [k for k in CFG["aliases"] if k.isdigit()]
    ranked = sorted((c for c in quotes if twse.COMMON_STOCK.match(c) and quotes[c]["value"]),
                    key=lambda c: -quotes[c]["value"])
    n_tw = 12 if QUICK else CFG["tw_universe_size"]
    tw_codes = list(dict.fromkeys(ranked[:n_tw] + [c for c in forced if c in quotes]))
    if not tw_codes:  # TWSE snapshot down -> fall back to the well-known names
        tw_codes = forced
    us_list = CFG["us_universe"][:10] if QUICK else CFG["us_universe"]
    # Over-the-counter (TPEx) stocks: the most actively traded ones join the Taiwan universe.
    otc_day = safe("櫃買中心 上櫃每日行情", lambda: otc_latest_day(trading_days), {}, "https://www.tpex.org.tw")
    otc_codes = sorted((c for c in otc_day if tpex.COMMON_STOCK.match(c) and c not in quotes),
                       key=lambda c: -(otc_day[c][3] * otc_day[c][4]))[:6 if QUICK else CFG.get("otc_universe_size", 30)]
    otc_margin, otc_qfii = {}, {}
    if otc_codes:
        otc_val = safe("櫃買中心 本益比／殖利率", tpex.valuation, {}, "https://www.tpex.org.tw")
        otc_rev = safe("櫃買中心 月營收", tpex.monthly_revenue, {}, "https://www.tpex.org.tw")
        otc_eps = safe("櫃買中心 每股盈餘", tpex.quarterly_eps, {}, "https://www.tpex.org.tw")
        otc_margin = safe("櫃買中心 融資融券", tpex.margin_latest, {}, "https://www.tpex.org.tw")
        otc_flags = safe("櫃買中心 處置／注意股與除權息預告", tpex.trading_flags, {}, "https://www.tpex.org.tw")
        otc_qfii = safe("櫃買中心 外資持股比率", tpex.foreign_holding, {}, "https://www.tpex.org.tw")
        for dst, src in ((valuation, otc_val), (revenue, otc_rev), (eps, otc_eps), (margin_now, otc_margin), (flags, otc_flags)):
            dst.update(src)
        for dst, keys in ((valuation, ("pe", "pb", "yield")), (eps, ("eps", "eps_period"))):
            for c in otc_codes:
                old = {k: (prev_otc.get(c) or {}).get(k) for k in keys}
                if c not in dst and any(v is not None for v in old.values()):
                    dst[c] = old
    tdcc = safe("集保 股權分散表（千張大戶）", lambda: update_tdcc(set(tw_codes) | set(otc_codes)), {}, "https://www.tdcc.com.tw")
    qfii = safe("證交所 外資持股比率", lambda: update_qfii(trading_days, set(tw_codes)), {}, "https://www.twse.com.tw")
    if otc_qfii:  # OTC holding comes as a snapshot only; its 20-day change builds up from the weekly archive
        qpath = DATA / "history" / "tw_qfii.json"
        qhist = read_json(qpath, {})
        qhist.setdefault(trading_days[-1], {}).update(
            {c: otc_qfii[c]["pct"] for c in otc_codes if c in otc_qfii and otc_qfii[c].get("pct") is not None})
        write_json(qpath, {d: qhist[d] for d in sorted(qhist)[-60:]})
        ref = max((d for d in qhist if len(trading_days) > 21 and d <= trading_days[-21]), default=None)
        for c in otc_codes:
            if c in otc_qfii:
                a, p = (qhist.get(ref) or {}).get(c) if ref else None, otc_qfii[c].get("pct")
                qfii[c] = {**otc_qfii[c], "chg20": round(p - a, 2) if a is not None and p is not None else None}
    derivs = safe("期交所 期貨選擇權歷史（散戶、十大交易人、基差、Put/Call、外資選擇權）",
                  lambda: update_derivs(today, twii["close"]), None, "https://www.taifex.com.tw") \
        or read_json(DATA / "history" / "tw_derivs.json", {})
    rev_hist = safe("公開資訊觀測站 月營收歷史", lambda: update_revenue_history(revenue, set(otc_codes)), {},
                    "https://mopsov.twse.com.tw")
    liab = safe("FinMind 合約負債（資產負債表）", lambda: update_contract_liabilities(tw_codes[:5] if QUICK else tw_codes, today),
                {}, "https://finmindtrade.com")

    # ---- 4. prices ----------------------------------------------------------
    actions = safe("證交所 除權除息計算結果（價格還原）", lambda: update_tw_actions(today), None, "https://www.twse.com.tw")
    if actions is None:
        actions = read_json(DATA / "history" / "tw_actions.json", {}).get("events", {})
    tw_frames = safe("證交所 個股每日行情", lambda: update_tw_prices(trading_days, tw_codes + tw_etfs, actions), {},
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
                         "industry": revenue.get(code, {}).get("industry") or industry_fallback(code)}
    if otc_codes:
        try:
            ev = tpex.ex_rights_recent()
            store = read_json(DATA / "history" / "tw_actions.json", {"done": [], "events": {}})
            for c, d in ev.items():
                store["events"].setdefault(c, {}).update(d)
                actions.setdefault(c, {}).update(d)
            write_json(DATA / "history" / "tw_actions.json", store)
        except Exception as e:  # noqa: BLE001 - recent OTC actions only; older ones are bridged by sanitising
            log(f"  OTC ex-rights: {e}")
        try:
            otc_flow_dates, otc_flows = otc_stock_flows(trading_days)
            for c, v in otc_flows.items():
                flow_map.setdefault(c, v)
            if len(otc_flow_dates) >= 15:  # 投信首買 for OTC names too (review 2026-11-14, chips-4)
                otc_first = leading.trust_first_buys({c: otc_flows[c] for c in otc_codes if c in otc_flows},
                                                     {c: bar[3] for c, bar in otc_day.items()})
                for r in otc_first:
                    r["name"] = (otc_margin.get(r["code"]) or {}).get("name") or r["code"]
                trust_first += otc_first
                first_by_code.update({r["code"]: r for r in otc_first})
        except Exception as e:  # noqa: BLE001 - OTC chips then come from the margin data only
            log(f"  OTC institutional trades: {e}")
    otc_frames = safe("櫃買中心 上櫃個股每日行情", lambda: update_otc_prices(trading_days, otc_codes, actions), {},
                      "https://www.tpex.org.tw") if otc_codes else {}
    otc_codes = [c for c in otc_codes if c in otc_frames]
    for code in otc_codes:
        sym = f"{code}.TWO"
        frames[sym] = otc_frames[code]
        meta[sym] = {"name": (otc_margin.get(code) or {}).get("name") or load_store(sym)[1].get("name") or code,
                     "market": "TW", "exchange": "OTC", "kind": "stock", "code": code, "scored": True,
                     "industry": revenue.get(code, {}).get("industry") or industry_fallback(code)}
    def us_prices(tkr: str) -> pd.DataFrame:
        raw = []
        try:
            raw = prices.cboe_intraday_raw(tkr)
        except Exception:  # noqa: BLE001 - intraday is optional
            pass
        df = clean(tkr, prices.append_session(prices.cboe_daily(tkr), raw), "US")
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
    form4 = safe("SEC Form 4 內部人交易", lambda: update_form4([s for s in stocks if meta[s]["market"] == "US"], ciks, today),
                 None, "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4") \
        or read_json(DATA / "history" / "us_form4.json", {"trades": []})
    since90 = (today - dt.timedelta(days=90)).isoformat()
    insider_us = {t: us_data.insider_summary([x for x in form4["trades"] if x["ticker"] == t], since90)
                  for t in {x["ticker"] for x in form4["trades"]}}

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
    # The technical score is published as a rank within each market (Taiwan also ranks the
    # distance from the 52-week high). The same panel, back over ~3 years, measures how often a
    # better rank went with beating the market's median stock - that is the only probability shown.
    by_mkt = {mk: [s for s in stocks if meta[s]["market"] == mk] for mk in ("TW", "US")}
    panels = {mk: scoring.tech_panel({s: tframes[s] for s in syms}, mk) for mk, syms in by_mkt.items() if syms}
    CALIB.clear()
    for mk, panel in panels.items():
        try:
            closes = pd.DataFrame({s: tframes[s]["close"] for s in panel.columns})
            CALIB[mk] = {h: v for h, v in market.fit_relative(panel, closes, scoring.HORIZONS).items() if v}
        except Exception as e:  # noqa: BLE001 - without a fit the relative chance is shown as 50%
            log(f"relative-strength calibration {mk} skipped: {e}")
        log(f"relative strength {mk}: " + ", ".join(f"{h} k={v['k']:.3f} fit={v['fit']:+.3f} edge={v['edge']}"
                                                   for h, v in CALIB.get(mk, {}).items()))
    tech_today = {s: float(p.iloc[-1][s]) for p in panels.values() for s in p.columns if pd.notna(p.iloc[-1][s])}
    # Residual momentum: each stock against its own market's index.
    bench_of = {"TW": "TWII", "US": "SPX"}
    imom = {s: scoring.residual_momentum(frames[s]["close"], frames[bench_of[meta[s]["market"]]]["close"])
            for s in stocks if bench_of.get(meta[s]["market"]) in frames}
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
        im = imom.get(s)
        if im is not None and len(im) and im.index[-1] >= df.index[-1] - pd.Timedelta(days=7):
            rec["imom"] = float(im.iloc[-1])
        rec["hi52"] = None if pd.isna(row.get("hi52")) else float(row["hi52"])
        if m["market"] == "TW" and code in tdcc:
            rec["tdcc"] = tdcc[code]
        if m["market"] == "TW" and code in qfii:
            rec.update({"qfii": qfii[code]["pct"], "qfii_room": qfii[code]["room"], "qfii_chg20": qfii[code]["chg20"]})
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
            vol = (quotes.get(code) or {}).get("volume")
            if daytrade.get(code) is not None and vol:
                rec["daytrade"] = round(min(1.0, daytrade[code] / vol), 3)
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
            # The quarterly table gives this year's cumulative EPS; price / P/E is the trailing four quarters.
            rec["eps_ttm"] = round(last / fund_in["pe"], 2) if (fund_in.get("pe") or 0) > 0 else None
            if fund_in.get("yield") and (fund_in.get("pe") or 0) > 0:  # dividends / trailing earnings
                rec["payout"] = round(fund_in["yield"] * fund_in["pe"] / 100, 3)
            mg = margins.get(code)
            if mg:
                rec.update({"gm": mg["gm"], "om": mg["om"], "nm": mg["nm"], "margin_period": mg["period"]})
            rec["yield_odd"] = scoring.odd_yield(fund_in.get("yield"), fund_in.get("pe")) or None
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
            if code in insider_us:
                rec["insider_us"] = insider_us[code]
            fund_in = us_fund.get(code, {})
            fund, fn = scoring.fund_us(fund_in)
            rec.update({k: fund_in.get(k) for k in ("pe", "eps", "eps_ttm", "eps_yoy", "rev_yoy", "eps_period")})
        buzz = scoring.buzz_score(rec["mentions"])
        # Attention is not a reason to buy; heavy attention on an already stretched stock is a caution.
        if rec["mentions"] >= 3 and ((rec["rsi"] or 0) >= 70 or (rec.get("margin_chg5") or 0) >= 0.05):
            notes.append((f"近兩週 {rec['mentions']} 集 Podcast 熱議且股價或融資過熱，留意追高風險", -0.08))
        sc = scoring.score_stock(tf, chips, fund, buzz, slow_chips=m["market"] == "US", tech=tech_today.get(s))
        rec.update(sc)
        rec["setup"] = scoring.setup_tag(sc["tactical"], sc["structural"], tactical_prev.get(s))
        allnotes = notes + cn + fn
        rec["reasons"] = [t for t, w in sorted(allnotes, key=lambda x: -x[1]) if w > 0][:4]
        rec["risks"] = [t for t, w in sorted(allnotes, key=lambda x: x[1]) if w < 0][:3]
        rec["plan"] = scoring.trade_plan(row, sc["horizons"]["1m"], sc["overall"])
        # 先觀察 (review 2026-11-14: product-5 + quant-6 + chips-3): facts a buyer should weigh before
        # following the rank. Weight 0; the conditions were fixed before any test and are archived weekly.
        watch = []
        holders = (rec.get("tdcc") or {}).get("holders")
        if holders and holders < 10000:
            watch.append(f"股東只有 {holders:,} 人")
        if (rec.get("r60") or 0) >= 0.30 and (rec.get("vol_ann") or 1) <= 0.35:
            watch.append(f"平穩急漲（60 日 {rec['r60']:+.0%}、年化波動 {rec['vol_ann']:.0%}）")
        if (rec["plan"].get("stop_pct") or 0) > 0.15:
            watch.append(f"停損距離 {rec['plan']['stop_pct']:.0%}")
        if (rec.get("daytrade") or 0) >= 0.70:
            watch.append(f"當沖比率 {rec['daytrade']:.0%}")
        rec["watch_first"] = watch or None
        rec["bars"] = len(df)
        records.append(rec)

    # ---- 8. picks / movers / backtest --------------------------------------
    # Rank within each market, the relative chance of beating that market's median stock, and
    # the official trading flags. These decide the picks; none of them is a forecast of direction.
    for mk in ("TW", "US"):
        rs = sorted((r for r in records if r["market"] == mk), key=lambda r: -r["overall"])
        cal = CALIB.get(mk, {})
        for i, r in enumerate(rs):
            x = 1 - 2 * i / max(1, len(rs) - 1)  # best = +1, worst = -1
            r["rank"] = [i + 1, len(rs)]
            r["edge"] = any(v.get("edge") for v in cal.values())
            for h, f in r["horizons"].items():
                c = cal.get(h)
                f["p_beat"] = round(market.p_beat(x, c["k"]), 3) if c and c.get("edge") else 0.5
                f["p_up"] = c["base_up"] if c else None
    for r in records:
        fl = flags.get(r["code"]) if r["market"] == "TW" else None
        r["flags"] = fl or {}
        if fl and fl.get("punish"):
            r["risks"] = [f"處置股（{fl['punish']['from'][5:]}～{fl['punish']['to'][5:]}），分盤撮合、須預收款券"] + r["risks"][:2]
    prev_picks = leading.previous_week_picks(read_json(DATA / "history" / "picks.json", []), trading_days[-1])
    rets = pd.DataFrame({s: frames[s]["close"].pct_change() for s in stocks}).tail(60)

    def pick(mk: str, n: int, keep_rank: int) -> list[dict]:
        """Best ranks first, with three guards: an earlier pick stays while its rank is within
        `keep_rank` (weekly churn cost about half the edge in the review's simulation), no stock
        under disposition, and no more than two picks that move together (60-day correlation
        >= 0.5 with each other) or `max_per_industry` from one industry."""
        cap = CFG["picks"]["max_per_industry"]
        pool = [r for r in records if r["market"] == mk and r["bars"] >= 120 and not r["flags"].get("punish")]
        pool.sort(key=lambda r: r["rank"][0])
        held = [r for r in pool if r["symbol"] in prev_picks and r["rank"][0] <= keep_rank]
        out: list[dict] = []
        for r in held + [r for r in pool if r not in held]:
            if len(out) >= n:
                break
            if sum(x["industry"] == r["industry"] for x in out) >= cap:
                continue
            close_peers = [x for x in out if r["symbol"] in rets and x["symbol"] in rets
                           and rets[r["symbol"]].corr(rets[x["symbol"]]) >= 0.5]
            if len(close_peers) >= 2:
                continue
            r["kept"] = r in held
            out.append(r)
        for r in pool:  # the guards must never leave the list short
            if len(out) >= n:
                break
            if r not in out:
                out.append(r)
        return out

    picks = pick("TW", CFG["picks"]["tw"], 30) + pick("US", CFG["picks"]["us"], 20)
    picks.sort(key=lambda r: (r["market"] != "TW", r["rank"][0]))  # scores of the two markets are not comparable
    pick_syms = {r["symbol"] for r in picks}
    bullish = sorted(records, key=lambda r: -r["overall"])[:12]
    bearish = sorted(records, key=lambda r: r["overall"])[:12]
    # Back-tests per market: the US had no measurable edge, which a pooled number would hide.
    bt = {mk: scoring.backtest({s: tframes[s] for s in syms}, panel=panels.get(mk)) for mk, syms in by_mkt.items()
          if len(syms) >= 25}
    # ...on today's universe, which flatters it: measure by how much against the point-in-time list
    sel_bias = safe("選樣偏誤（今日名單對當時名單）", lambda: research.selection_bias(
        read_json(DATA / "history" / "tw_universe_pit.json", {}), shard_get("pit_closes"),
        [s.split(".")[0] for s in by_mkt.get("TW", []) if s.endswith(".TW")]), None)

    # ---- 8b. ETFs and podcast-hot names -------------------------------------
    etf_records = []
    for s, m in meta.items():
        if m["kind"] != "etf" or len(frames[s]) < 60:
            continue
        if newest[m["market"]] is not None and frames[s].index[-1] < newest[m["market"]] - pd.Timedelta(days=7):
            continue  # stale prices: leave it out rather than score it
        er = etf_record(s, frames[s], tframes[s], m, flow_map, mentions.get(s, 0))
        if m["market"] == "TW":
            nv = etf_nav.get(m.get("code") or s.split(".")[0])
            if nv:
                er.update({"prem": nv["prem"], "aum": nv["aum"], "flow_day": nv["flow"], "nav_time": nv["time"]})
            # cash paid over the last 12 months, from the ex-dividend factors (price basis: 1 / product - 1)
            ev = {d: f for d, f in (actions.get(m.get("code") or s.split(".")[0]) or {}).items()
                  if frames[s].index[-1] - pd.Timedelta(days=365) < pd.Timestamp(d) <= frames[s].index[-1]}
            if ev:
                er["dist12"] = round(1 / math.prod(ev.values()) - 1, 4)
        etf_records.append(er)
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
    funds.rescore(fund_records)  # peer-relative scores need the whole set (review 2026-11-14, fund-7)
    SOURCES.append({"name": "鉅亨網 基金淨值與同類排名", "ok": bool(fund_records) or not fund_list,
                    "url": "https://fund.cnyes.com", "note": f"{len(fund_records)}/{len(fund_list)} 檔取得"})
    if not fund_records:  # source down: keep last week's table
        fund_records = read_json(DATA / "funds.json", [])
    fund_records.sort(key=lambda r: -(r["score"] if r.get("score") is not None else -1))

    # ---- 8d. leading indicators and the record of past picks ------------------
    lead = build_leading(frames, tframes, stocks, meta, flows, quotes, today, derivs)
    # One cohort per ISO week: a re-run within the week replaces that week's list.
    wk = lambda d: dt.date.fromisoformat(d).isocalendar()[:2]  # noqa: E731
    cohorts = [c for c in read_json(DATA / "history" / "picks.json", []) if wk(c["date"]) != wk(trading_days[-1])]
    # A pick's stop is set when it enters and afterwards only moves up (review 2026-11-14, risk-4):
    # re-deriving it each week from a rising moving average let it drift lower than where it began.
    last_cohort = {x["symbol"]: x for x in (cohorts[-1]["picks"] if cohorts else [])}
    for r in picks:
        ps, pl = last_cohort.get(r["symbol"], {}).get("stop"), r.get("plan") or {}
        if ps and pl.get("stop") is not None and ps > pl["stop"]:
            if ps < r["close"]:
                pl["stop"], pl["stop_pct"] = ps, round((r["close"] - ps) / r["close"], 4)
                pl["size"] = min(scoring.MAX_POSITION, scoring.RISK_BUDGET / pl["stop_pct"])
                pl["stop_note"] = "沿用入選以來最高的停損（只上移、不下移）"
            else:
                pl["stop_hit"] = ps
                r["risks"] = [f"收盤已低於先前的停損 {ps:,.2f}"] + r["risks"][:2]
    cohorts.append({"date": trading_days[-1],
                    "picks": [{"symbol": r["symbol"], "name": r["name"], "market": r["market"], "close": r["close"],
                               "stop": (r.get("plan") or {}).get("stop"), "watch": r.get("watch_first")}
                              for r in picks]})
    cohorts = cohorts[-60:]
    write_json(DATA / "history" / "picks.json", cohorts)
    track = leading.cohort_results(cohorts[:-1], frames, {"TW": "TWII", "US": "SPX"})
    # following the lists by rules fixed in advance: next-day open to next-day open, net of costs
    follow = safe("精選名單的實際追蹤（事先寫定的規則）", lambda: leading.follow_track(
        cohorts, frames, {"TW": "0050.TW", "US": "SPY"},
        {mk: [s for s in stocks if meta[s]["market"] == mk and meta[s]["kind"] == "stock"] for mk in ("TW", "US")}), None)
    chg = leading.pick_changes([r["symbol"] for r in picks], cohorts[:-1], trading_days[-1])
    for r in picks:
        r["diff"] = chg["by_symbol"].get(r["symbol"])
    ref_names = {p["symbol"]: p["name"] for c in cohorts[:-1] for p in c["picks"]}
    picks_diff = {"ref_date": chg["ref_date"], "new": sum(1 for v in chg["by_symbol"].values() if v["new"]),
                  "dropped": [{"symbol": x, "name": ref_names.get(x, x)} for x in chg["dropped"]]}
    bt_imom = scoring.factor_ic(imom, {s: frames[s]["close"] for s in imom}) if len(imom) >= 25 else {}

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
            c = df["close"].tail(1260)
            item["horizons"] = {h: scoring.forecast(float(row["close"]), row["vol"], scoring.clamp(
                scoring.blend(h, {"tech": float(row["t_slow"]), "chips": chips, "fund": None, "buzz": None})), d,
                p_up=float((c.shift(-d) / c - 1).dropna().gt(0).mean()))
                for h, d in scoring.HORIZONS.items()}
            item["reasons"] = [t for t, _ in scoring.tech_reasons(row)]
        indices_out.append(item)

    mkt = safe("市場安全（風險分數、週期、連動、準確度）", lambda: build_market(
        frames, tframes, stocks, meta, flows, lead, tw_flow, records, tactical_prev, indices_out,
        trading_days[-1], today), None) or read_json(DATA / "market.json", {})
    narrative = build_narrative(indices_out, tw_flow, picks, bullish, bearish, (mkt or {}).get("risk"))
    weekly = safe("每週快照與本週變化", lambda: weekly_snapshot(trading_days[-1], mkt, lead, picks, records), None)
    portfolio = safe("精選組合風險", lambda: portfolio_panel(picks, records, tframes, frames), None)

    def brief(r: dict) -> dict:
        out = {k: r.get(k) for k in ("code", "name", "market", "industry", "close", "chg5d", "r20", "overall", "label",
                                     "tactical", "structural", "setup", "rank", "reasons", "risks", "watch_first")}
        fl = r.get("flags") or {}
        out["p_beat_1m"] = r["horizons"]["1m"].get("p_beat")
        out["official_flags"] = [k for k in ("punish", "notice", "near_punish", "exdiv") if fl.get(k)]
        return out

    write_json(ROOT / ".cache" / "digest.json", {
        "data_date": trading_days[-1],
        "indices": [{k: i.get(k) for k in ("name", "close", "chg5d", "r20", "ytd", "rsi", "horizons")}
                    for i in indices_out],
        "tw_flow": tw_flow, "narrative": narrative, "backtest": bt,
        "leading": [{**{k: c.get(k) for k in ("name", "value_text", "change_text", "reading", "tone")},
                     "evidence": {k: (c.get("evidence") or {}).get(k) for k in ("grade", "text", "pct")}} for c in lead["cards"]],
        "weekly": weekly, "selection_bias": sel_bias,
        "portfolio": {mk: {k: v for k, v in x.items() if k != "weights"} for mk, x in (portfolio or {}).items()},
        "regime": lead.get("regime"), "calibration": {mk: {h: {k: v[k] for k in ("k", "edge", "base_up", "brier", "brier_coin")}
                                                         for h, v in c.items()} for mk, c in CALIB.items()},
        "picks_diff": picks_diff, "calendar": build_calendar(trading_days[-1], flags, meta),
        "market_safety": market_digest(mkt),
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
        if old.get("applied"):
            payload["applied"] = old["applied"]
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

    # Cross-page badges: what the other tabs say about each stock, so nobody has to remember it.
    tagmap: dict[str, list] = {}
    put = lambda sym, text, tone: tagmap.setdefault(sym, []).append([text, tone])  # noqa: E731
    if flow_map:
        for rows, text, tone in ((top_flow(0, 1), "外資買超前 15", 1), (top_flow(0, -1), "外資賣超前 15", -1),
                                 (top_flow(1, 1), "投信買超前 15", 1), (top_flow(1, -1), "投信賣超前 15", -1)):
            for x in rows:
                put(x["code"] + ".TW", text, tone)
    for x in trust_first:
        put(x["code"] + ".TW", "投信首買", 1)
    # official trading flags first: they change what can actually be traded
    soon = (dt.date.fromisoformat(trading_days[-1]) + dt.timedelta(days=14)).isoformat()
    for r in records + etf_records:
        fl = flags.get(r["code"]) if r["market"] == "TW" else None
        if not fl:
            continue
        if fl.get("punish"):
            put(r["symbol"], f"處置中至 {fl['punish']['to'][5:].replace('-', '/')}", -1)
        if fl.get("notice"):
            put(r["symbol"], "注意股", -1)
        if fl.get("near_punish"):
            put(r["symbol"], "可能列入處置", -1)
        ex = fl.get("exdiv")
        if ex and trading_days[-1] < ex["date"] <= soon:
            put(r["symbol"], f"{ex['date'][5:].replace('-', '/')} 除{ex['kind'] or '息'}", 0)
    fresh_sbl = lead.get("sbl") if not (lead.get("sbl") or {}).get("stale") else None  # no badges from old data
    for x in ((fresh_sbl or {}).get("up") or []):
        put(x["code"] + ".TW", "借券大增", -1)
    for x in ((fresh_sbl or {}).get("down") or []):
        put(x["code"] + ".TW", "借券回補", 1)
    for x in margin_risk[:25]:
        put(x["code"] + ".TW", "融資斷頭壓力", -1)
    for x in (mkt or {}).get("accumulating") or []:
        put(x["symbol"], "動能累積", 1)
    for x in (mkt or {}).get("weakening") or []:
        put(x["symbol"], "可能轉弱", -1)
    for r in records:
        if r.get("rev_cross") == "up":
            put(r["symbol"], "營收剛轉強", 1)
        if (r.get("guru_net") or 0) >= 2:
            put(r["symbol"], "名人基金加碼", 1)
        if (r.get("guru_net") or 0) <= -2:
            put(r["symbol"], "名人基金減碼", -1)
        if (r.get("congress_buys") or 0) - (r.get("congress_sells") or 0) >= 2:
            put(r["symbol"], "議員淨買進", 0)
        iu = r.get("insider_us") or {}
        if iu.get("buys"):
            put(r["symbol"], "內部人買進", 1)
        if (iu.get("sell_unplanned") or 0) >= 1e7:
            put(r["symbol"], "內部人大額賣出", -1)
        if r.get("qfii_chg20") is not None and abs(r["qfii_chg20"]) >= 2:
            put(r["symbol"], f"外資持股 20 日{'增' if r['qfii_chg20'] > 0 else '減'} {abs(r['qfii_chg20']):.1f} 個百分點",
                1 if r["qfii_chg20"] > 0 else -1)
        if r.get("mentions", 0) >= 2:
            hot_too = r["mentions"] >= 3 and ((r.get("rsi") or 0) >= 70 or (r.get("margin_chg5") or 0) >= 0.05)
            put(r["symbol"], "Podcast 過熱" if hot_too else "Podcast 熱議", -1 if hot_too else 0)
        r["tags"] = tagmap.get(r["symbol"], [])
    for r in etf_records:
        r["tags"] = tagmap.get(r["symbol"], [])

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
        "gurus": gurus, "tw_insiders": sorted(tw_ins, key=lambda r: -(r["pledge"] or 0)),
        "us_insiders": {"trades": [t for t in form4["trades"] if t["date"] >= since90][:200],
                        "by_ticker": sorted(({"ticker": k, "name": meta.get(k, {}).get("name", k), **v} for k, v in insider_us.items()),
                                            key=lambda r: -(r["buy_value"] + r["sell_value"]))}})
    write_json(DATA / "podcasts.json", {"shows": shows, "mentions": mentions, "hot": hot,
                                        "names": {s: meta[s]["name"] for s in mentions if s in meta}})
    write_json(DATA / "etf.json", etf_records)
    write_json(DATA / "funds.json", fund_records)
    write_json(DATA / "leading.json", {"cards": lead["cards"], "series": lead["series"],
                                       "options": lead.get("options"), "regime": lead.get("regime")})
    write_json(DATA / "tools.json", tools)
    if mkt:
        write_json(DATA / "market.json", mkt)
    slim = lambda r: {k: v for k, v in r.items() if k not in ("bars",)}  # noqa: E731
    write_json(DATA / "universe.json", [slim(r) for r in sorted(records, key=lambda r: -r["overall"])])
    calendar = build_calendar(trading_days[-1], flags, meta)
    quality = {"cleaned": {s: n for s, n in CLEAN_LOG.items() if s in meta},
               "coverage": {mk: {k: round(sum(r.get(k) is not None for r in records if r["market"] == mk)
                                           / max(1, sum(r["market"] == mk for r in records)), 3)
                                 for k in (("pe", "eps", "rev_yoy", "foreign5", "margin") if mk == "TW"
                                           else ("pe", "eps_ttm", "rev_yoy"))} for mk in ("TW", "US")},
               "behind": {mk: [r["symbol"] for r in records if r["market"] == mk and newest[mk] is not None
                               and pd.Timestamp(r["date"]) < newest[mk]] for mk in ("TW", "US")}}
    safe("證交所 除權息歷史回補（供下週使用）", lambda: backfill_tw_actions(today), None, "https://www.twse.com.tw")
    write_json(DATA / "summary.json", {
        "generated_at": now.isoformat(timespec="seconds"), "data_date": trading_days[-1],
        "calendar": calendar, "quality": quality,
        "next_update": next_wednesday(today).isoformat(), "quick": QUICK,
        "indices": indices_out, "tw_flow": tw_flow, "narrative": narrative,
        "picks": [slim(r) for r in picks],
        "bullish": [slim(r) for r in bullish], "bearish": [slim(r) for r in bearish],
        "backtest": bt, "backtest_imom": bt_imom, "calibration": CALIB, "picks_diff": picks_diff,
        "selection_bias": sel_bias, "weekly": weekly, "portfolio": portfolio, "follow": follow,
        "weights": scoring.WEIGHTS, "weights_us": scoring.WEIGHTS_SLOW_CHIPS,
        "sources": SOURCES, "picks_track": track,
        "counts": {"tw": sum(r["market"] == "TW" for r in records),
                   "us": sum(r["market"] == "US" for r in records), "etf": len(etf_records),
                   "fund": len(fund_records)}})
    bad = [s["name"] for s in SOURCES if not s["ok"]]
    log(f"done: {len(records)} stocks, {len(etf_records)} ETFs, {len(fund_records)} funds, {len(picks)} picks, "
        f"{len(hot)} podcast-hot, "
        f"failed sources: {bad or 'none'}")
    return 0


def portfolio_panel(picks: list[dict], records: list[dict], tframes: dict, frames: dict) -> dict:
    """The picks held together, per market and combined in TWD at a 60/40 split (review 2026-11-14, risk-1)."""
    fx = frames["USDTWD"]["close"].pct_change() if "USDTWD" in frames else None
    out: dict = {}
    parts = []
    for mk, bench in (("TW", "TWII"), ("US", "SPX")):
        ps = [r for r in picks if r["market"] == mk and r["symbol"] in tframes]
        if len(ps) < 2 or bench not in frames:
            continue
        w = research.pick_weights([(r.get("plan") or {}).get("stop_pct") for r in ps])
        rets = pd.DataFrame({r["symbol"]: tframes[r["symbol"]]["close"].pct_change() for r in ps}).tail(250)
        res = research.portfolio_risk(rets, w, frames[bench]["close"].pct_change())
        if not res:
            continue
        uni = [r for r in records if r["market"] == mk and r.get("kind") != "etf"]
        med = lambda rows, k: float(np.nanmedian([r[k] for r in rows if r.get(k) is not None])) if rows else None  # noqa: E731
        fund_sym = "0050.TW" if mk == "TW" else "SPY"
        if fund_sym in frames:
            res["stress"] = research.stress_replay({r["symbol"]: float(x) for r, x in zip(ps, w)},
                                                   {r["symbol"]: tframes[r["symbol"]]["close"] for r in ps},
                                                   frames[fund_sym]["close"], frames[bench]["close"])
        res.update({"weights": {r["symbol"]: round(float(x), 4) for r, x in zip(ps, w)},
                    "hi52": med(ps, "hi52"), "hi52_all": med(uni, "hi52"), "r60": med(ps, "r60"), "r60_all": med(uni, "r60")})
        out[mk] = res
        if mk == "US" and fx is not None:
            rets = ((1 + rets).mul(1 + fx.reindex(rets.index).fillna(0.0), axis=0) - 1)  # what a TWD investor gets
        parts.append((0.6 if mk == "TW" else 0.4, rets, w))
    if len(parts) == 2:
        rets = pd.concat([p[1] for p in parts], axis=1)
        w = np.concatenate([p[0] * p[2] for p in parts])
        both = research.portfolio_risk(rets, w, frames["TWII"]["close"].pct_change())
        if both:
            out["ALL"] = both
    return out


def weekly_snapshot(data_date: str, mkt: dict | None, lead: dict, picks: list[dict], records: list[dict]) -> dict:
    """One snapshot per ISO week, history/weekly/<YYYY-Www>.json (review 2026-11-14, product-1).

    A re-run inside the week replaces that week's file; earlier weeks are never rewritten, so
    "since last week" always means the last snapshot of an earlier week. Returns what changed."""
    folder = DATA / "history" / "weekly"
    y, w, _ = dt.date.fromisoformat(data_date).isocalendar()
    key = f"{y}-W{w:02d}"
    mkt = mkt or {}
    risk, fut = mkt.get("risk") or {}, mkt.get("futures") or {}
    snap = {"week": key, "date": data_date,
            "risk": {"score": risk.get("score"), "name": risk.get("name"),
                     "parts": {p["key"]: [p["weight"], p["risk"], p["name"]] for p in risk.get("parts") or []}},
            "regime": (mkt.get("regime") or {}).get("state"), "breadth": (mkt.get("breadth") or {}).get("value"),
            "futures": {k: fut.get(k) for k in ("net_oi", "trust_oi", "all_oi")},
            "cards": {c["key"]: {"name": c["name"], "value": c["value_text"], "tone": c["tone"],
                                 "grade": (c.get("evidence") or {}).get("grade"), "pct": (c.get("evidence") or {}).get("pct")}
                      for c in lead.get("cards") or []},
            "picks": [{"symbol": r["symbol"], "name": r["name"], "rank": r.get("rank")} for r in picks],
            "ranks": {r["symbol"]: r["rank"] for r in records if r.get("rank")}}
    write_json(folder / f"{key}.json", snap)
    earlier = sorted(p.stem for p in folder.glob("*.json") if p.stem < key)
    if not earlier:
        return {"week": key, "ref": None}
    old = read_json(folder / f"{earlier[-1]}.json", {})
    out: dict = {"week": key, "ref": old.get("week"), "ref_date": old.get("date")}
    rn, ro = snap["risk"], old.get("risk") or {}
    if rn.get("score") is not None and ro.get("score") is not None:
        tot = sum(v[0] for v in rn["parts"].values()) or 1
        moves = [(k, v[2], ro["parts"][k][1], v[1], v[0] * (v[1] - ro["parts"][k][1]) / tot)
                 for k, v in rn["parts"].items() if k in (ro.get("parts") or {})]
        out["risk"] = {"now": rn["score"], "prev": ro["score"], "name": rn["name"], "prev_name": ro.get("name"),
                       "drivers": [{"name": nm, "from": a, "to": b, "effect": round(e, 1)}
                                   for _k, nm, a, b, e in sorted(moves, key=lambda m: -abs(m[4]))[:3] if abs(e) >= 0.5]}
    oc = old.get("cards") or {}
    out["cards"] = [{"key": k, "name": c["name"], "from": oc[k]["pct"], "to": c["pct"]}
                    for k, c in snap["cards"].items()
                    if k in oc and c.get("pct") is not None and oc[k].get("pct") is not None
                    and abs(c["pct"] - oc[k]["pct"]) >= 0.30]
    was = {p["symbol"]: p for p in old.get("picks") or []}
    now = {p["symbol"] for p in snap["picks"]}
    by_sym = {r["symbol"]: r for r in records}

    def why_out(sym: str) -> str:
        r = by_sym.get(sym)
        if not r:
            return "已不在觀察名單"
        if (r.get("flags") or {}).get("punish"):
            return "處置股"
        rk = r.get("rank")
        cap = 30 if r["market"] == "TW" else 20
        return f"排名降到 {rk[0]}／{rk[1]}" if rk and rk[0] > cap else "產業或相關性上限"

    out["picks_in"] = [p for p in snap["picks"] if p["symbol"] not in was]
    out["picks_out"] = [{"symbol": s_, "name": p["name"], "why": why_out(s_)} for s_, p in was.items() if s_ not in now]
    return out


# ----------------------------- market safety ----------------------------- #
def update_futures_history(last_day: dt.date) -> dict[str, dict]:
    """Foreign TX net open interest per day. The first run backfills about 13 months, later runs the last one.

    The query must end on a day that has data (the site returns nothing for a range ending
    after its latest report), so it ends on the last trading day rather than today.
    """
    path = DATA / "history" / "tw_futures.json"
    hist = read_json(path, {})
    months = 1 if QUICK or len(hist) >= 200 else 13
    end = last_day
    for _ in range(months):
        start = end - dt.timedelta(days=30)
        try:
            hist.update(market.futures_history(start.isoformat(), end.isoformat()))
        except Exception as e:  # noqa: BLE001 - one missing month only shortens the history
            log(f"  futures history {start}..{end}: {str(e)[:80]}")
        end = start - dt.timedelta(days=1)
    hist = {d: hist[d] for d in sorted(hist)[-520:]}
    write_json(path, hist)
    if not hist or (last_day - dt.date.fromisoformat(max(hist))).days > 7:
        raise RuntimeError(f"futures history ends at {max(hist) if hist else 'nothing'}")  # stale: do not score it
    return hist


RISK_READ = {
    "trend": lambda x: f"加權指數處於「{market.REGIMES[x['regime']]}」，距一年高點 {x['drawdown']:+.1%}",
    "breadth": lambda x: f"廣度指數 {x['breadth']:.0f}（0–100，越低代表越少個股站上均線）",
    "foreign": lambda x: f"外資近 20 日買賣超占成交值 {x['foreign20_ratio']:+.1%}，連續 {abs(x.get('foreign_streak') or 0)} 日"
                         + ("買超" if (x.get("foreign_streak") or 0) > 0 else "賣超"),
    "futures": lambda x: f"台指期淨未平倉 {x['fut_net']:+,.0f} 口，" + ("是過去一年最偏空的水準" if x["fut_pct"] <= 0.01 else
                                                               f"比過去一年 {1 - x['fut_pct']:.0%} 的日子更偏空"),
    "margin": lambda x: f"大盤融資維持率估算約 {x['maint']:.0%}（170% 以上視為安全，140% 以下危險）",
    "vol": lambda x: f"VIX {x['vix']:.1f}" + (f"，期限結構 {x['vix_term']:.2f}" if x.get("vix_term") is not None else ""),
    "credit": lambda x: f"美國高收益債利差 {x['oas']:.2f}%，10 日 {x.get('oas_chg10') or 0:+.2f} 個百分點",
    "intl": lambda x: f"SOXX 20 日 {x['soxx_r20']:+.1%}、那斯達克 {x['ndq_r20']:+.1%}、美元／台幣 {x['fx_r20']:+.1%}",
    "liquidity": lambda x: f"Fed 淨流動性 13 週變化 {x['liq_chg13']:+.1%}（+3% 以上視為安全，−5% 以下危險）",
}


def build_market(frames: dict, tframes: dict, stocks: list[str], meta: dict, flows: list[dict], lead: dict,  # noqa: C901, PLR0915
                 tw_flow: dict, records: list[dict], tactical_prev: dict, indices_out: list[dict],
                 data_date: str, today: dt.date) -> dict:
    """Is the market safe, which stocks are gathering or losing momentum, and how good were past calls."""
    twii = frames["TWII"]["close"]
    iso = lambda d: d.strftime("%Y-%m-%d")  # noqa: E731
    r20 = lambda s: float(s.iloc[-1] / s.iloc[-21] - 1) if len(s) > 21 else None  # noqa: E731
    x: dict = {}

    # breadth index and whether the index's highs / lows are confirmed by it
    b = leading.breadth({s: tframes[s] for s in stocks if meta[s]["market"] == "TW"}, n=250)
    ad = [r for r in flows if r.get("adv") is not None][-10:]
    ad_ratio = sum(r["adv"] for r in ad) / max(1, sum(r["dec"] for r in ad)) if ad else None
    bidx = market.breadth_index(b, ad_ratio)
    breakout = market.breakout_check(twii, bidx)
    x.update({"breadth": float(bidx.iloc[-1]), "breakout": breakout,
              "breadth_chg5": float(bidx.iloc[-1] - bidx.iloc[-6]) if len(bidx) > 6 else None})
    breadth_out = {"value": x["breadth"], "chg5": x["breadth_chg5"], "above20": float(b["above20"].iloc[-1]),
                   "above60": float(b["above60"].iloc[-1]), "ad_ratio": ad_ratio, "breakout": breakout,
                   "series": [{"date": iso(d), "v": float(v), "twii": float(twii.get(d)) if d in twii.index else None}
                              for d, v in bidx.items()]}

    # regime
    states = market.regime_states(twii)
    reg = market.regime_now(states)
    x.update({"regime": reg["state"], "drawdown": float(twii.iloc[-1] / twii.tail(250).max() - 1)})
    reg.update({"ma60": float(twii.rolling(60).mean().iloc[-1]), "ma200": float(twii.rolling(200).mean().iloc[-1]),
                "close": float(twii.iloc[-1]), "drawdown": x["drawdown"],
                "stats": market.forward_stats(states, twii), "sample_from": iso(states.index[0])})

    # foreign futures
    fut_out, fut_risk = None, None
    fh = safe("期交所 外資台指期未平倉歷史", lambda: update_futures_history(dt.date.fromisoformat(data_date)), None, "https://www.taifex.com.tw")
    if fh:
        fs = pd.Series({pd.Timestamp(d): v["net_oi"] for d, v in fh.items()}).sort_index()
        x.update({"fut_net": float(fs.iloc[-1]), "fut_pct": leading.percentile(fs),
                  "fut_chg5": float(fs.iloc[-1] - fs.iloc[-6]) if len(fs) > 6 else None})
        fut_risk = (1 - fs.rolling(250, min_periods=120).rank(pct=True)) * 100
        # foreign + trusts + dealers: the trusts' long (leveraged-ETF hedge) offsets most of the foreign short
        last = fh[max(fh)]
        both = pd.Series({pd.Timestamp(d): v["net_oi"] + v["trust_oi"] + v.get("dealer_oi", 0.0)
                          for d, v in fh.items() if "trust_oi" in v}).sort_index()
        x["fut_all_chg5"] = float(both.iloc[-1] - both.iloc[-6]) if len(both) > 6 else None
        fut_out = {"date": iso(fs.index[-1]), "net_oi": x["fut_net"], "chg5": x["fut_chg5"],
                   "trust_oi": last.get("trust_oi"), "dealer_oi": last.get("dealer_oi"),
                   "all_oi": float(both.iloc[-1]) if len(both) and both.index[-1] == fs.index[-1] else None,
                   "all_chg20": float(both.iloc[-1] - both.iloc[-21]) if len(both) > 21 else None,
                   "chg20": float(fs.iloc[-1] - fs.iloc[-21]) if len(fs) > 21 else None, "pct": x["fut_pct"],
                   "min_250": float(fs.tail(250).min()), "max_250": float(fs.tail(250).max()),
                   "series": [{"date": iso(d), "v": float(v), "twii": float(twii.get(d)) if d in twii.index else None}
                              for d, v in fs.tail(250).items()]}

    # global financial stress (OFR)
    stress_out, fsi = None, safe("OFR 金融壓力指數", market.ofr_fsi, None, "https://www.financialresearch.gov/financial-stress-index/")
    if fsi is not None and len(fsi) > 30:
        last = fsi.iloc[-1]
        x.update({"fsi": float(last["fsi"]), "fsi_chg20": float(last["fsi"] - fsi["fsi"].iloc[-21])})
        stress_out = {"date": iso(fsi.index[-1]), "value": x["fsi"], "chg20": x["fsi_chg20"],
                      "pct_5y": leading.percentile(fsi["fsi"], 1250),
                      "parts": [{"name": n, "value": float(last[k]), "chg20": float(last[k] - fsi[k].iloc[-21])}
                                for k, n in (("credit", "信用"), ("equity", "股票估值"), ("funding", "融資"),
                                             ("safe", "避險資產"), ("vol", "波動率"))],
                      "series": [[iso(d), float(v)] for d, v in fsi["fsi"].tail(500).items()]}

    # the rest of today's readings
    turnover, f20 = tw_flow.get("turnover"), tw_flow.get("foreign_20d")
    soxx, ndq, fxs, vix = (frames[k]["close"] if k in frames else None for k in ("SOXX", "NDQ", "USDTWD", "VIX"))
    rg, vt, oas_full = lead.get("regime") or {}, lead.get("vix_term_full"), lead.get("oas_full")
    nl = lead.get("liq_full")
    x["liq_chg13"] = float(nl.iloc[-1] / nl.iloc[-14] - 1) if nl is not None and len(nl) > 14 else None
    x.update({"foreign20_ratio": f20 / (turnover * 20) if turnover and f20 is not None else None,
              "foreign_streak": tw_flow.get("foreign_streak"), "maint": tw_flow.get("maint"),
              "vix": float(vix.iloc[-1]) if vix is not None else None,
              "vix_term": float(vt.iloc[-1]) if vt is not None else None,
              "oas": rg.get("oas"), "oas_chg10": rg.get("oas_chg10"),
              "soxx_r20": r20(soxx) if soxx is not None else None, "ndq_r20": r20(ndq) if ndq is not None else None,
              "fx_r20": r20(fxs) if fxs is not None else None})
    parts = market.risk_parts(x)
    for p in parts:
        p["reading"] = RISK_READ[p["key"]](x)
    total = market.risk_total(parts)

    # the score's own history, from the components that reach back years, and how the index did afterwards
    hs = {"trend": states.map(market.REGIME_RISK).astype(float)}
    if vix is not None:
        hs["vol"] = market.vol_risk(vix)
    if oas_full is not None:
        hs["credit"] = market.credit_risk(oas_full)
    if all(v is not None for v in (soxx, ndq, fxs)):
        al = lambda s: s.reindex(s.index.union(twii.index)).ffill().reindex(twii.index)  # noqa: E731
        hs["intl"] = market.intl_risk(al(soxx).pct_change(20), al(ndq).pct_change(20), al(fxs).pct_change(20)).dropna()
    if nl is not None:
        hs["liquidity"] = market.liquidity_risk(nl.pct_change(13)).dropna()
    hist = market.risk_history(hs, states.index)
    risk_out = {"score": total, **market.risk_level(total), "parts": parts,
                "history_parts": [n for k, n, _w in market.RISK_PARTS if k in hs],
                "buckets": market.bucket_stats(hist, twii), "sample_from": iso(hist.index[0]) if len(hist) else None,
                "series": [{"date": iso(d), "v": float(v), "twii": float(twii.get(d))} for d, v in hist.tail(500).items()]}

    # cross-market linkage
    link = None
    if all(v is not None for v in (soxx, ndq, fxs)):
        link = market.linkage(twii, [("SOXX", "費城半導體（SOXX）", soxx, True), ("NDQ", "那斯達克綜合", ndq, True),
                                     ("USDTWD", "美元／台幣", fxs, False)])
        if link:
            for d, s in zip(link["drivers"], (soxx, ndq, fxs)):
                d.update({"date": iso(s.index[-1]), "r5": float(s.iloc[-1] / s.iloc[-6] - 1), "r20": r20(s)})

    # forecasts: log today's, score the ones whose horizon has passed
    log_path = DATA / "history" / "forecasts.json"
    flog = read_json(log_path, {"stk": {}, "idx": {}})
    hz = list(scoring.HORIZONS)
    flog["stk"][data_date] = {r["symbol"]: [r["close"]] + [r["horizons"][h]["score"] for h in hz] for r in records}
    flog["idx"][data_date] = {i["symbol"]: {"close": i["close"], "h": {h: [i["horizons"][h]["p_up"], i["horizons"][h]["low"],
                                                                         i["horizons"][h]["high"]] for h in hz}}
                              for i in indices_out if i.get("horizons")}
    for k in ("stk", "idx"):
        flog[k] = {d: flog[k][d] for d in sorted(flog[k])[-30:]}
    write_json(log_path, flog)
    past = lambda k: {d: v for d, v in flog[k].items() if d < data_date}  # noqa: E731
    accuracy = {"backtest": CALIB.get("TW") or {}, "backtest_us": CALIB.get("US") or {},
                "live": market.evaluate_log(past("stk"), frames, scoring.HORIZONS),
                "index_log": market.evaluate_index_log(past("idx"), frames, scoring.HORIZONS)[-40:],
                "snapshots": len(flog["stk"]), "first_snapshot": min(flog["stk"])}

    acc, weak = market.momentum_lists(records, tactical_prev)

    # alerts need the previous run to tell what changed
    hist_path = DATA / "history" / "market.json"
    runs = [r for r in read_json(hist_path, []) if r["date"] != data_date]
    # "previous" means the last run of an earlier ISO week: a re-run in the same week is not last week
    wk = lambda d: dt.date.fromisoformat(d).isocalendar()[:2]  # noqa: E731
    prev = next((r for r in reversed(runs) if wk(r["date"]) < wk(data_date)), {})
    x.update({"risk": total, "risk_prev": prev.get("risk"), "regime_prev_run": prev.get("regime")})
    alerts = market.alerts(x)
    # every input and part score is kept, so a changed formula can later be re-tested on what it saw
    keep = ("foreign20_ratio", "foreign_streak", "maint", "breadth", "fut_net", "fut_all_chg5", "vix", "vix_term",
            "oas", "oas_chg10", "soxx_r20", "ndq_r20", "fx_r20", "liq_chg13", "drawdown")
    runs.append({"date": data_date, "risk": total, "breadth": x["breadth"], "regime": reg["state"],
                 "fut_net": x.get("fut_net"), "alerts": len([a for a in alerts if a["level"] == 2]),
                 "parts": {p["key"]: p["risk"] for p in parts},
                 "inputs": {k: (round(x[k], 4) if isinstance(x.get(k), float) else x.get(k)) for k in keep}})
    write_json(hist_path, runs[-260:])

    tw = next((i for i in indices_out if i["symbol"] == "TWII"), None)
    return {"date": data_date, "risk": risk_out, "risk_prev": prev.get("risk"), "risk_prev_date": prev.get("date"),
            "regime": reg, "breadth": breadth_out,
            "futures": fut_out, "stress": stress_out, "linkage": link, "accuracy": accuracy,
            "accumulating": acc, "weakening": weak, "alerts": alerts, "runs": runs[-26:],
            "index_outlook": {h: {**tw["horizons"][h], "days": d,
                                  "base": (reg["stats"].get(reg["state"]) or {}).get(h)}
                              for h, d in scoring.HORIZONS.items()} if tw else None}


def market_digest(m: dict) -> dict | None:
    """The part of market.json the weekly commentary may quote."""
    if not m:
        return None
    strip = lambda d, *drop: None if d is None else {k: v for k, v in d.items() if k not in drop}  # noqa: E731
    return {"risk": strip(m["risk"], "series"), "risk_prev": m.get("risk_prev"),
            "regime": m["regime"], "breadth": strip(m["breadth"], "series"),
            "futures": strip(m["futures"], "series"), "stress": strip(m["stress"], "series"),
            "linkage": m["linkage"], "alerts": m["alerts"], "index_outlook": m["index_outlook"],
            "accuracy": {"backtest": {h: strip(v, "bins") for h, v in m["accuracy"]["backtest"].items()},
                         "live": m["accuracy"]["live"]},
            "accumulating": [{k: r[k] for k in ("code", "name", "market", "chg5d", "tactical", "tags")} for r in m["accumulating"][:8]],
            "weakening": [{k: r[k] for k in ("code", "name", "market", "chg5d", "tactical", "tags")} for r in m["weakening"][:8]]}


def update_revenue_history(revenue: dict, otc: set[str] | None = None) -> dict[str, dict]:
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
        have = hist.get(ym, {})
        for board, need in (("sii", sum(not c in (otc or ()) for c in have) < 500),
                            ("otc", bool(otc) and sum(c in otc for c in have) < len(otc) * 0.5)):
            if not need:
                continue
            try:
                hist.setdefault(ym, {}).update(leading.mops_revenue(ym, board))
            except Exception as e:  # noqa: BLE001 - archive page missing or refused: try again next run
                log(f"  revenue {ym} {board}: {e}")
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


CALIB: dict[str, dict] = {}  # market -> horizon -> relative-strength fit (market.fit_relative)

CARD_GROUP = {"liq": "資金與跨市場", "otc": "市場廣度", "adr": "資金與跨市場", "fx": "資金與跨市場", "real": "資金與跨市場", "curve": "資金與跨市場",
              "hy": "資金與跨市場", "semis": "資金與跨市場", "vixts": "資金與跨市場",
              "breadth_tw": "市場廣度", "breadth_us": "市場廣度", "ad": "市場廣度",
              "retail": "期貨、選擇權與放空", "top10": "期貨、選擇權與放空", "basis": "期貨、選擇權與放空",
              "pcr": "期貨、選擇權與放空", "optwall": "期貨、選擇權與放空", "sbl": "期貨、選擇權與放空",
              "fo": "期貨、選擇權與放空"}


def _card(key: str, name: str, value_text: str, change_text: str, reading: str, tone: int = 0,
          spark=None) -> dict:
    return {"key": key, "name": name, "value_text": value_text, "change_text": change_text,
            "reading": reading, "tone": tone,
            "spark": [] if spark is None else [round(float(x), 4) for x in spark]}


def build_leading(frames: dict, tframes: dict, stocks: list[str], meta: dict, flows: list[dict],  # noqa: C901, PLR0915
                  quotes: dict, today: dt.date, derivs: dict | None = None) -> dict:
    """Indicators that tend to move before prices do. Shown for context only: none of
    them feeds the scores, because this site has no history yet to test them on.
    Each indicator is built on its own, so one failing source costs one card."""
    cards, series, extra = [], {}, {"sbl": None, "options": None, "regime": None, "liq_full": None}
    dv = derivs or {}
    ev: dict[str, tuple[pd.Series, int]] = {}  # card key -> (daily history of what it shows, +1 if higher reads bullish)

    def dseries(key: str) -> pd.Series:
        return pd.Series({pd.Timestamp(d): v[key] for d, v in dv.items() if v.get(key) is not None}).sort_index()

    def pct_note(key: str, value: float) -> str:
        s_ = dseries(key)
        if len(s_) < 120:
            return ""
        s_ = s_.tail(250)
        return f"・高於一年 {float((s_ <= value).mean()):.0%} 的日子"
    pp = lambda x: f"{x * 100:+.1f} 個百分點"  # noqa: E731
    dated = lambda sr, n=250: [[d.strftime("%Y-%m-%d"), round(float(v), 4)] for d, v in sr.tail(n).items()]  # noqa: E731
    by_date = {r["date"]: r for r in flows}

    def adr():
        prem = leading.adr_premium(frames["TSM"]["close"], frames["2330.TW"]["close"], frames["USDTWD"]["close"])
        now, ago, pct = float(prem.iloc[-1]), float(prem.iloc[-21]), leading.percentile(prem)
        series["adr"] = dated(prem)
        ev["adr"] = (prem, 1)
        cards.append(_card("adr", "台積電 ADR 溢價", f"{now:.1%}", f"20 日 {pp(now - ago)}",
                           f"高於過去一年 {pct:.0%} 的日子。溢價擴大代表海外買盤對台積電的出價高於台股，"
                           "常被視為外資回補的前兆；收斂則相反。", 1 if now > ago else -1, prem.tail(60)))

    def fx():
        c = frames["USDTWD"]["close"]
        r20, weak = float(c.iloc[-1] / c.iloc[-21] - 1), bool(c.iloc[-1] > c.tail(20).mean())
        ev["fx"] = (c.pct_change(20), -1)
        cards.append(_card("fx", "美元／台幣", f"{c.iloc[-1]:.2f}", f"20 日 {r20:+.1%}",
                           ("台幣走貶、匯率在月線之上，外資匯出壓力仍在。" if weak else
                            "台幣走升、匯率跌破月線，通常伴隨外資匯入。")
                           + f"資料日 {c.index[-1]:%m/%d}（Fed 公布有數日落差）。", -1 if weak else 1, c.tail(60)))

    def real_yield():
        ry = prices.fred_daily("DFII10")["close"]
        ev["real"] = (ry.diff(20), -1)
        chg = float(ry.iloc[-1] - ry.iloc[-21])
        cards.append(_card("real", "美國 10 年期實質殖利率", f"{ry.iloc[-1]:.2f}%", f"20 日 {chg:+.2f} 個百分點",
                           "實質利率上升會壓縮成長股評價，下降則有利科技股與新興市場資金流入。",
                           -1 if chg > 0.05 else 1 if chg < -0.05 else 0, ry.tail(60)))

    def vix_term():
        ts = leading.term_structure(frames["VIX"]["close"], prices.cboe_daily("_VIX3M")["close"])
        now = float(ts.iloc[-1])
        series["vix_term"] = dated(ts)
        ev["vixts"] = (ts, -1)
        extra["vix_term_full"] = ts
        cards.append(_card("vixts", "VIX 期限結構（VIX ÷ VIX3M）", f"{now:.2f}", f"5 日 {now - float(ts.iloc[-6]):+.2f}",
                           ("高於 1：短期恐慌高過三個月後的預期，屬於恐慌狀態；由高點回落常出現在急跌尾聲。"
                            if now >= 1 else "低於 1：屬正常狀態，短期避險需求不高。"), -1 if now >= 1 else 0, ts.tail(60)))

    def breadth_of(mk: str, label: str):
        fs = {s: tframes[s] for s in stocks if meta[s]["market"] == mk}
        b = leading.breadth(fs)
        a20, a60 = float(b["above20"].iloc[-1]), float(b["above60"].iloc[-1])
        ev[f"breadth_{mk.lower()}"] = (b["above60"], 1)
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
                           dseries("retail_mtx").tail(120) if len(dseries("retail_mtx")) else
                           [x["retail_mtx"] for x in flows if x.get("retail_mtx") is not None][-60:]))
        cards[-1]["change_text"] += pct_note("retail_mtx", r)

    def pcr():
        rows = leading.put_call()
        for x in rows:
            if x["date"] in by_date:
                by_date[x["date"]]["pcr_oi"] = round(x["pcr_oi"], 4)
        now = rows[-1]["pcr_oi"]
        cards.append(_card("pcr", "台指選擇權 Put/Call 未平倉比", f"{now:.0%}", f"5 日 {pp(now - rows[-6]['pcr_oi'])}",
                           ("高於 100%：賣權未平倉多於買權，一般解讀為下檔有支撐、偏多。" if now >= 1 else
                            "低於 100%：買權未平倉多於賣權，一般解讀為上檔有壓、偏空。")
                           + ("今天是週選結算日，結算日的 P/C 平均比其他日子高約 14 個百分點，讀數要打折。"
                              if pd.Timestamp(rows[-1]["date"]).weekday() == 2 else
                              "每週三週選結算日的 P/C 常跳高約 14 個百分點，比較時要避開結算日。"),
                           1 if now >= 1.1 else -1 if now < 0.9 else 0,
                           dseries("pcr_oi").tail(120) if len(dseries("pcr_oi")) > 20 else [x["pcr_oi"] for x in rows]))
        cards[-1]["change_text"] += pct_note("pcr_oi", now)

    def foreign_options():
        c, p_ = dseries("fo_call"), dseries("fo_put")
        if not len(c) or not len(p_):
            raise RuntimeError("no foreign options history")
        d = c.index[-1]
        cn, pn = float(c.iloc[-1]), float(p_.get(d, float("nan")))
        tone = leading.options_stance(cn, pn)
        c5 = float(c.iloc[-1] - c.iloc[-6]) if len(c) > 6 else 0.0
        cards.append(_card("fo", "外資台指選擇權淨未平倉", f"買權 {cn:+,.0f}・賣權 {pn:+,.0f} 口",
                           f"買權 5 日 {c5:+,.0f}・{d:%m-%d}",
                           ("外資淨買進買權、淨賣出賣權：兩者都在指數上漲時獲利，部位偏多。" if tone > 0 else
                            "外資淨賣出買權、淨買進賣權：兩者都在指數下跌時獲利，部位偏空。" if tone < 0 else
                            "買權與賣權方向不一致，可能是價差或避險組合，看不出明確方向。")
                           + "外資常用選擇權搭配期貨與現貨避險，單看選擇權容易誤判，要和期貨未平倉一起看。",
                           tone, (c - p_.reindex(c.index).fillna(0)).tail(120)))

    def sbl():
        mdates = [r["date"] for r in flows if r.get("margin_bal")]
        if QUICK or len(mdates) < 6:
            return
        def cached_sbl(d: str) -> dict:  # local cache: a re-run the same day asks the exchange for nothing
            path = ROOT / ".cache" / "twse_sbl" / f"{d}.json"
            if path.exists():
                return read_json(path, {})
            data = leading.sbl_table(d)
            write_json(path, data)
            return data

        got = twse.fetch_days([mdates[-1], mdates[-6]], cached_sbl, "sbl", log)
        if mdates[-1] not in got or mdates[-6] not in got:
            raise RuntimeError("the exchange did not return both days (throttled)")
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

    def liq():
        nl = leading.net_liquidity(prices.fred_daily("WALCL")["close"], prices.fred_daily("WTREGEN")["close"],
                                   prices.fred_daily("RRPONTSYD")["close"])
        now, c4, c13 = float(nl.iloc[-1]), float(nl.iloc[-1] / nl.iloc[-5] - 1), float(nl.iloc[-1] / nl.iloc[-14] - 1)
        extra["liq_full"] = nl
        series["net_liq"] = dated(nl, 260)
        ev["liq"] = (nl.pct_change(13), 1)
        cards.append(_card("liq", "Fed 淨流動性", f"{now:.2f} 兆美元", f"4 週 {c4:+.1%}・13 週 {c13:+.1%}",
                           ("近一季明顯增加：留在金融體系的資金變多，對股票等風險資產是順風。" if c13 >= 0.01 else
                            "近一季明顯減少：資金被抽走，風險資產的評價較難擴張。" if c13 <= -0.01 else
                            "近一季變化不大，資金面中性。")
                           + f"算法：聯準會資產負債表 − 財政部帳戶 − 逆回購，每週更新（資料日 {nl.index[-1]:%m/%d}）。",
                           1 if c13 >= 0.01 else -1 if c13 <= -0.01 else 0, nl.tail(104)))

    def otc():
        path = DATA / "history" / "tpex.json"
        hist = {**read_json(path, {}), **leading.tpex_index()}
        hist = {d: hist[d] for d in sorted(hist)[-520:]}
        write_json(path, hist)
        o = pd.Series({pd.Timestamp(d): v for d, v in hist.items()})
        ratio = (o / frames["TWII"]["close"]).dropna()
        if len(ratio) < 6:
            raise RuntimeError("OTC index history too short")
        r5 = float(ratio.iloc[-1] / ratio.iloc[-6] - 1)
        o5 = float(o.iloc[-1] / o.iloc[-6] - 1)
        series["otc_rel"] = dated(ratio)
        ev["otc"] = (ratio.pct_change(5), 1)
        cards.append(_card("otc", "櫃買相對強弱（櫃買 ÷ 加權）", f"櫃買 {o.iloc[-1]:.1f}", f"5 日 {o5:+.1%}・相對加權 {r5:+.1%}",
                           ("櫃買跑贏加權：中小型股比權值股強，市場願意承擔風險。" if r5 >= 0.01 else
                            "櫃買落後加權：資金躲在權值股，中小型股失血；指數上漲時這種背離代表漲勢不扎實。" if r5 <= -0.01 else
                            "櫃買與加權同步。") + "櫃買以中小型與內資為主，常比加權指數先轉弱或先止穩。",
                           1 if r5 >= 0.01 else -1 if r5 <= -0.01 else 0, ratio.tail(60)))

    def curve():
        yc = prices.fred_daily("T10Y2Y")["close"]
        ev["curve"] = (yc, 1)
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
        extra["oas_full"] = oas
        series["hy_oas"] = dated(oas)
        series["semis_rel"] = dated(ratio)
        ev["hy"] = (oas.diff(10), -1)
        ev["semis"] = (ratio / ratio.rolling(60).mean() - 1, 1)
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
                           f"{w['put_wall']:,.0f} – {w['call_wall']:,.0f}",
                           f"Max Pain {w['max_pain']:,.0f}・該合約 P/C {w['put_oi'] / w['call_oi']:.0%}",
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
                           dseries("top10").tail(120) if len(dseries("top10")) else
                           [x["top10"] for x in flows if x.get("top10") is not None][-60:]))
        cards[-1]["change_text"] += pct_note("top10", r)

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
                           dseries("basis").tail(120) if len(dseries("basis")) else
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
        ad_s = pd.Series({pd.Timestamp(r["date"]): r["adv"] / max(1, r["dec"]) for r in rows}).sort_index()
        ev["ad"] = (ad_s.rolling(10).mean(), 1)
        line, acc = [], 0
        for r in rows[-120:]:
            acc += r["adv"] - r["dec"]
            line.append(acc)
        twii = frames["TWII"]["close"]
        idx_up = len(twii) > len(ten) and float(twii.iloc[-1]) > float(twii.iloc[-len(ten) - 1])
        cards.append(_card("ad", "全市場漲跌家數（上市普通股）", f"近 {len(ten)} 日漲跌比 {ratio:.2f}",
                           f"{last['date'][5:]} 單日 漲 {last['adv']}・跌 {last['dec']}",
                           ("指數上漲但下跌家數較多：漲勢集中在少數權值股，廣度沒有跟上。" if idx_up and ratio < 0.95 else
                            "指數下跌但上漲家數較多：賣壓集中在權值股，中小型股相對有撐。" if not idx_up and ratio > 1.05 else
                            "上漲家數明顯多於下跌，漲勢普遍。" if ratio >= 1.2 else
                            "下跌家數明顯多於上漲，賣壓普遍。" if ratio <= 0.8 else "漲跌家數接近，沒有明顯方向。"),
                           1 if ratio >= 1.2 else -1 if ratio <= 0.8 else 0, line))

    for name, fn, url in (("FRED Fed 淨流動性", liq, "https://fred.stlouisfed.org"),
                          ("櫃買中心 櫃買指數", otc, "https://www.tpex.org.tw"),
                          ("台積電 ADR 溢價", adr, ""), ("美元／台幣動能", fx, ""),
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
                          ("期交所 外資選擇權部位", foreign_options, "https://www.taifex.com.tw"),
                          ("期交所 選擇權未平倉分布", options, "https://openapi.taifex.com.tw"),
                          ("證交所 借券賣出餘額", sbl, "https://www.twse.com.tw")):
        safe(f"領先指標：{name}", fn, None, url)
    if extra["sbl"] is None:  # keep last run's tables on screen, marked as old, rather than an empty panel
        prev = read_json(DATA / "flows.json", {}).get("sbl")
        if prev:
            extra["sbl"] = {**prev, "stale": True}
    # Evidence grades: does what each card shows have any relation to the TAIEX afterwards?
    twii_c = frames["TWII"]["close"]
    if len(dv):
        ev.setdefault("retail", (dseries("retail_mtx"), -1))
        ev.setdefault("top10", (dseries("top10"), 1))
        ev.setdefault("pcr", (dseries("pcr_oi"), 1))
        ev.setdefault("fo", ((dseries("fo_call") - dseries("fo_put")).dropna(), 1))
        b_ = dseries("basis")
        ev.setdefault("basis", ((b_ / twii_c.reindex(b_.index)).dropna(), 1))
    for c in cards:
        c["group"] = CARD_GROUP.get(c["key"], "其他")
        x_, orient = ev.get(c["key"], (None, 0))
        try:
            c["evidence"] = research.evidence(x_, twii_c, orient)
        except Exception as e:  # noqa: BLE001 - a failed test only means no grade
            log(f"  evidence {c['key']}: {e}")
            c["evidence"] = {"grade": "D", "text": research.GRADE_TEXT["D"], "n": 0}
        # without evidence the card shows where the value sits in its own history, not a direction
        if not (c["evidence"]["grade"] in ("A", "B") and c["evidence"].get("agree")):
            c["tone"] = 0
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
                    bear: list[dict], mkt_risk: dict | None = None) -> list[dict]:
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
        parts = [f"{hz[h]}一個標準差區間 {f['low']:,.0f}–{f['high']:,.0f}（近 5 年同期間上漲比率 {f['p_up']:.0%}）"
                 for h, f in i["horizons"].items() if f.get("p_up") is not None]
        if parts:
            out.append({"title": f"{i['name']}展望", "text": "；".join(parts)
                        + "。上漲比率是歷史統計，不是預測。"})

    rk = (mkt_risk or {})
    adv = (f"短期回檔風險為「{rk['name']}」（{rk['score']:.0f} 分），此等級對應的經驗持股區間為{rk['exposure']}（未經驗證，只是預設值，請依自己的承受度調整）。"
           if rk.get("name") and rk.get("score") is not None else "")
    if bull and bear:
        top = [r["name"] for r in bull if r["market"] == "TW"][:4]
        low = [r["name"] for r in bear if r["market"] == "TW"][:4]
        adv += f"台股同市場排名最前：{'、'.join(top)}；排名最後：{'、'.join(low)}。"
    adv += "排名只比較股票之間的相對強弱，不是方向預測；所有價位皆為參考。"
    out.append({"title": "操作建議", "text": adv})
    return out


if __name__ == "__main__":
    sys.exit(main())
