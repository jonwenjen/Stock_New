"""Price history.

Primary sources are official / openly published feeds:
  * Cboe delayed-quotes CDN  - US stocks, ETFs and Cboe-disseminated indices (daily + 1-min intraday)
  * FRED (St. Louis Fed) CSV - macro series (yields, FX, oil, bitcoin, Nasdaq Composite)
  * TWSE                     - Taiwan stocks / TAIEX (see twse.py; merged into the on-disk store)
Yahoo Finance is only an optional enrichment (deep Taiwan history + Taiwan intraday).
It rate-limits aggressively, so a circuit breaker turns it off for the rest of the
run after the first refusal instead of retrying.
"""
from __future__ import annotations

import datetime as dt
import io
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd

from . import http

http.HOST_INTERVAL.setdefault("cdn-api.cboe.com", 1.2)  # it answers 429 when polled faster
http.HOST_INTERVAL.setdefault("fred.stlouisfed.org", 0.5)

CBOE = "https://cdn-api.cboe.com/api/global/delayed_quotes/charts"  # cdn.cboe.com now redirects here
COLS = ["open", "high", "low", "close", "volume"]


def _r(x: float) -> float:
    return round(float(x), 4 if abs(x) < 10 else 2)


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    for c in ("open", "high", "low"):  # index feeds sometimes publish 0 for the open
        df[c] = df[c].where(df[c] > 0, df["close"])
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    df["volume"] = df["volume"].fillna(0)
    return df[~df.index.duplicated(keep="last")].sort_index()[COLS]


# ----------------------------- Cboe ----------------------------- #
def cboe_symbol(ticker: str) -> str:
    return ticker.replace("-", ".")


def cboe_intraday_raw(ticker: str) -> list[dict]:
    j = http.get_json(f"{CBOE}/intraday/{quote(cboe_symbol(ticker))}.json", retries=1)
    return j.get("data") or []


def cboe_daily(ticker: str, scale: float = 1.0) -> pd.DataFrame:
    """Full split-adjusted daily history; the latest session is appended from intraday bars."""
    j = http.get_json(f"{CBOE}/historical/{quote(cboe_symbol(ticker))}.json", retries=2)
    df = pd.DataFrame(j["data"])
    df[COLS] = df[COLS].apply(pd.to_numeric, errors="coerce")
    df = df.set_index(pd.to_datetime(df["date"]))[COLS]
    df[["open", "high", "low", "close"]] *= scale
    return _finish(df)


def session_complete(raw: list[dict], now: dt.datetime | None = None) -> bool:
    """False while the session in the intraday file is still trading (New York time).

    A half-finished day must not be stored as a daily bar; early closes are covered
    because any session dated before today counts as complete.
    """
    last = dt.datetime.fromisoformat(raw[-1]["datetime"])
    now = now or dt.datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    return last.date() < now.date() or last.time() >= dt.time(15, 55)


def append_session(df: pd.DataFrame, raw: list[dict], scale: float = 1.0,
                   now: dt.datetime | None = None) -> pd.DataFrame:
    """Add the session covered by intraday bars when the daily file has not caught up yet."""
    if not raw or not session_complete(raw, now):
        return df
    day = pd.Timestamp(raw[0]["datetime"][:10])
    if len(df) and day <= df.index[-1]:
        return df
    p = [b["price"] for b in raw]
    bar = {"open": p[0]["open"] * scale, "high": max(x["high"] for x in p) * scale,
           "low": min(x["low"] for x in p) * scale, "close": p[-1]["close"] * scale,
           "volume": sum(b["volume"].get("stock_volume") or 0 for b in raw)}
    return _finish(pd.concat([df, pd.DataFrame([bar], index=[day])]))


def intraday_5m(raw: list[dict], scale: float = 1.0) -> list[list]:
    """1-minute Cboe bars -> [[epoch_local, o, h, l, c, v], ...] in 5-minute buckets."""
    if not raw:
        return []
    df = pd.DataFrame({
        "t": pd.to_datetime([b["datetime"] for b in raw]),
        "open": [b["price"]["open"] for b in raw], "high": [b["price"]["high"] for b in raw],
        "low": [b["price"]["low"] for b in raw], "close": [b["price"]["close"] for b in raw],
        "volume": [b["volume"].get("stock_volume") or 0 for b in raw]}).set_index("t")
    g = df.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                 "volume": "sum"}).dropna(subset=["close"])
    return [[int(t.timestamp()), _r(o * scale), _r(h * scale), _r(l * scale), _r(c * scale), int(v)]
            for t, o, h, l, c, v in g.itertuples()]


# ----------------------------- FRED ----------------------------- #
def fred_daily(series_id: str) -> pd.DataFrame:
    """Close-only series; OHLC are all set to the published value."""
    txt = http.get_text("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id}, retries=2)
    df = pd.read_csv(io.StringIO(txt))
    df.columns = ["date", "close"]
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna().set_index(pd.to_datetime(df.dropna()["date"]))
    for c in ("open", "high", "low"):
        df[c] = df["close"]
    df["volume"] = 0.0
    return _finish(df)


# ----------------------------- Yahoo (optional) ----------------------------- #
class Yahoo:
    """Best-effort enrichment with a circuit breaker: one refusal disables it for the run."""

    enabled = True

    @classmethod
    def _chart(cls, symbol: str, rng: str, interval: str) -> dict | None:
        if not cls.enabled:
            return None
        try:
            j = http.get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}",
                              params={"range": rng, "interval": interval}, retries=0, timeout=25)
            res = (j.get("chart") or {}).get("result")
            return res[0] if res else None
        except Exception as e:  # noqa: BLE001 - throttled or unreachable: stop asking
            cls.enabled = False
            print(f"[yahoo] disabled for this run: {e}", flush=True)
            return None

    @staticmethod
    def _frame(res: dict) -> pd.DataFrame:
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({"ts": res.get("timestamp") or [], **{c: q.get(c) for c in COLS}})
        return df.dropna(subset=["open", "high", "low", "close"])

    @classmethod
    def daily(cls, symbol: str) -> pd.DataFrame | None:
        # range=max silently degrades to monthly bars, so ask for ten years and verify.
        res = cls._chart(symbol, "10y", "1d")
        if not res:
            return None
        df = cls._frame(res)
        off = int(res["meta"].get("gmtoffset") or 0)
        df.index = pd.to_datetime(df["ts"] + off, unit="s").dt.normalize()
        df = _finish(df)
        return df if is_daily(df) else None

    @classmethod
    def intraday(cls, symbol: str) -> list[list]:
        res = cls._chart(symbol, "5d", "5m")
        if not res:
            return []
        df = cls._frame(res)
        off = int(res["meta"].get("gmtoffset") or 0)
        return [[int(t + off), _r(o), _r(h), _r(l), _r(c), int(v or 0)]
                for t, o, h, l, c, v in df[["ts"] + COLS].itertuples(index=False)]


def is_daily(df: pd.DataFrame) -> bool:
    """True when the rows really are daily bars (typical gap of a few days at most)."""
    if len(df) < 30:
        return False
    gaps = df.index.to_series().diff().dt.days.dropna()
    return float(gaps.median()) <= 4 and float((gaps > 10).mean()) < 0.02


# ----------------------------- on-disk store / chart payload ----------------------------- #
def rows_to_frame(rows: list[list]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([]))
    df = pd.DataFrame(rows, columns=["date"] + COLS)
    return df.set_index(pd.to_datetime(df["date"]))[COLS].astype(float)


def merge(old: pd.DataFrame | None, new: pd.DataFrame | None) -> pd.DataFrame:
    """Union of two histories; `new` wins on overlapping dates."""
    parts = [p for p in (old, new) if p is not None and len(p)]
    if not parts:
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([]))
    df = pd.concat(parts)
    return df[~df.index.duplicated(keep="last")].sort_index()[COLS]


def weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Monday-to-Friday bars, each stamped with the first trading day of its week."""
    f = df.assign(_d=df.index)
    g = f.groupby(df.index.to_period("W-FRI")).agg(
        {"_d": "first", "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return g.set_index(pd.DatetimeIndex(g.pop("_d"))).dropna(subset=["close"])


def _rows(f: pd.DataFrame) -> list[list]:
    return [[d.strftime("%Y-%m-%d"), _r(o), _r(h), _r(l), _r(c), int(v)]
            for d, o, h, l, c, v in f[COLS].itertuples()]


def compact(df: pd.DataFrame, daily_years: int = 5, old_weekly: list[list] | None = None) -> dict:
    """Chart payload: daily bars for recent years ('d') + weekly bars for everything older ('w').

    `old_weekly` carries forward weekly bars already stored for dates before df starts.
    """
    if df.empty:
        return {"d": [], "w": old_weekly or []}
    cut = df.index[-1] - pd.DateOffset(years=daily_years, days=7)
    cut -= pd.Timedelta(days=(cut.weekday() + 2) % 7)  # back to a Saturday: never split a trading week
    recent, old = df[df.index >= cut], df[df.index < cut]
    w = _rows(weekly(old)) if len(old) else []
    if old_weekly:
        first = w[0][0] if w else recent.index[0].strftime("%Y-%m-%d")
        w = [r for r in old_weekly if r[0] < first] + w
    return {"d": _rows(recent), "w": w}


# ----------------------------- integrity: bad bars and corporate actions ----------------------------- #
def _easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(y, month, day + 1)


def nyse_holidays(year: int) -> set[dt.date]:
    """Full-day NYSE closures for a year (the regular rules; one-off closures are not included)."""
    def nth(month: int, weekday: int, n: int) -> dt.date:
        first = dt.date(year, month, 1)
        return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))

    def last(month: int, weekday: int) -> dt.date:
        nxt = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
        return nxt - dt.timedelta(days=(nxt.weekday() - weekday) % 7)

    def observed(d: dt.date) -> dt.date:
        return d - dt.timedelta(days=1) if d.weekday() == 5 else d + dt.timedelta(days=1) if d.weekday() == 6 else d

    out = {observed(dt.date(year, 1, 1)), nth(1, 0, 3), nth(2, 0, 3), _easter(year) - dt.timedelta(days=2),
           last(5, 0), observed(dt.date(year, 7, 4)), nth(9, 0, 1), nth(11, 3, 4), observed(dt.date(year, 12, 25))}
    if year >= 2022:
        out.add(observed(dt.date(year, 6, 19)))
    return out


def daily_limit(code: str) -> float | None:
    """Largest possible one-day move for a Taiwan listing, with a little slack; None = no limit known."""
    if code.endswith(("L", "R")) and code.startswith("00"):
        return 0.21          # leveraged / inverse ETFs move up to twice the index limit
    if code.isdigit() and len(code) == 4:
        return 0.105         # common stocks: 10% since June 2015
    if code.startswith("00"):
        return 0.105         # domestic ETFs (foreign-index ETFs have no limit, but none is tracked here)
    return None


def adjust_before(df: pd.DataFrame, date: pd.Timestamp, factor: float) -> pd.DataFrame:
    """Multiply prices strictly before `date` by `factor` (volume is left alone)."""
    df = df.copy()
    m = df.index < date
    df.loc[m, ["open", "high", "low", "close"]] *= factor
    return df


def sanitize(df: pd.DataFrame, *, market: str, code: str = "", calendar: pd.DatetimeIndex | None = None,
             events: dict[str, float] | None = None, applied: list[str] | None = None,
             spike_limit: float = 0.35) -> tuple[pd.DataFrame, list[str], list[str], float]:
    """Remove bars that cannot be real and make the history continuous across corporate actions.

    1. Weekend bars go (except for 24/7 assets: market "CRYPTO").
    2. Bars on days the market was closed go: Taiwan days missing from `calendar` (the
       TAIEX's own dates, within the range it covers); US days on NYSE holidays.
    3. Known corporate actions (`events`: ex-date -> reference price / previous close,
       from the exchange) are applied once each - prices before the ex-date are scaled.
       `applied` lists the keys already in the stored history so they are never applied twice.
    4. Spikes - a jump beyond the market's daily limit (or `spike_limit` where there is none)
       that is undone within five sessions - are deleted.
    5. Taiwan only: a jump beyond the daily limit that persists cannot be a trading move,
       so it is an unannounced action or a seam between differently adjusted sources;
       earlier prices are rescaled so that day shows no change.

    Returns (clean frame, applied keys, notes, product of all scalings done this call), the
    last so older weekly bars kept elsewhere can be scaled the same way.
    """
    notes: list[str] = []
    applied = list(applied or [])
    total = 1.0
    if df is None or df.empty:
        return df, applied, notes, total
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    if market != "CRYPTO":
        wk = df.index.dayofweek >= 5
        if wk.any():
            notes.append(f"刪除週末 K 棒 {int(wk.sum())} 根")
            df = df[~wk]
    if market == "TW" and calendar is not None and len(calendar):
        cal = set(calendar)
        inside = (df.index >= calendar.min()) & (df.index <= calendar.max())
        off = inside & ~df.index.isin(list(cal))
        if off.any():
            notes.append(f"刪除休市日 K 棒 {int(off.sum())} 根")
            df = df[~off]
    if market == "US":
        hol = set()
        for y in sorted(set(df.index.year)):
            hol |= nyse_holidays(int(y))
        off = pd.Series(df.index.date, index=df.index).isin(hol).to_numpy()
        if off.any():
            notes.append(f"刪除美股休市日 K 棒 {int(off.sum())} 根")
            df = df[~off]
    for d, f in sorted((events or {}).items()):
        key = f"{d}:{f:.6f}"
        ts = pd.Timestamp(d)
        if key in applied or not (0.05 < f < 1.5) or ts <= df.index[0] or ts > df.index[-1]:
            continue
        df = adjust_before(df, ts, f)
        applied.append(key)
        total *= f
        notes.append(f"{d} 除權息還原 ×{f:.4f}")
    lim = daily_limit(code) if market == "TW" else None
    thr = lim or spike_limit
    tol = lim or 0.15  # "undone" = back within this distance of the price before the jump
    # 4. spikes
    i = 1
    while i < len(df):
        c = df["close"].to_numpy()
        r = c[i] / c[i - 1] - 1
        if abs(r) > thr:
            back = next((k for k in range(1, 6) if i + k < len(c) and abs(c[i + k] / c[i - 1] - 1) < tol), None)
            if back is not None:
                notes.append(f"刪除異常跳動 {df.index[i]:%Y-%m-%d} 起 {back} 根（{r:+.0%}）")
                df = df.drop(df.index[i:i + back])
                continue
        i += 1
    # 5. persistent breaks (Taiwan has hard daily limits, so these are never real moves)
    if lim:
        c = df["close"]
        r = c / c.shift(1) - 1
        for ts in r.index[(r.abs() > lim).to_numpy()][::-1]:
            pos = df.index.get_loc(ts)
            f = float(df["close"].iloc[pos] / df["close"].iloc[pos - 1])
            df = adjust_before(df, ts, f)
            total *= f
            notes.append(f"{ts:%Y-%m-%d} 未公告的價格斷層 {f - 1:+.0%}，已接續")
    return df, applied, notes, total


def scale_rows(rows: list[list], factor: float) -> list[list]:
    """Scale stored [date, o, h, l, c, v] rows (e.g. the weekly bars older than the daily store)."""
    if abs(factor - 1) < 1e-12:
        return rows
    return [[r[0], _r(r[1] * factor), _r(r[2] * factor), _r(r[3] * factor), _r(r[4] * factor), r[5]] for r in rows]
