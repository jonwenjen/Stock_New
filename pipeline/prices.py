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

import io
from urllib.parse import quote

import pandas as pd

from . import http

http.HOST_INTERVAL.setdefault("cdn.cboe.com", 1.2)  # it answers 429 when polled faster
http.HOST_INTERVAL.setdefault("fred.stlouisfed.org", 0.5)

CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/charts"
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


def append_session(df: pd.DataFrame, raw: list[dict], scale: float = 1.0) -> pd.DataFrame:
    """Add the session covered by intraday bars when the daily file has not caught up yet."""
    if not raw:
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
