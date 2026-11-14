"""Unit tests for the pure parts of the pipeline (no network)."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from pipeline import indicators as ind
from pipeline import market, media, scoring, twse, us_data


def make_df(prices):
    c = pd.Series(prices, index=pd.bdate_range("2023-01-02", periods=len(prices)), dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6})


def trend(n=400, drift=0.002, seed=1):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.012, n)))


# ---------------- indicators ----------------
def test_rsi_bounds_and_direction():
    up = ind.rsi(pd.Series(np.linspace(100, 200, 60)))
    down = ind.rsi(pd.Series(np.linspace(200, 100, 60)))
    assert up.iloc[-1] == 100 and down.iloc[-1] < 1
    noisy = ind.rsi(pd.Series(trend(300, 0)))
    assert noisy.dropna().between(0, 100).all()


def test_sma_and_range_position():
    s = pd.Series(range(1, 11), dtype=float)
    assert ind.sma(s, 5).iloc[-1] == 8
    assert math.isnan(ind.sma(s, 5).iloc[3])
    pos = ind.range_position(pd.Series(np.linspace(1, 2, 100)), 60)
    assert pos.iloc[-1] == pytest.approx(1.0)


def test_streak():
    assert twse.streak([1, -2, 3, 4, 5]) == 3
    assert twse.streak([5, -1, -1]) == -2
    assert twse.streak([1, 2, 0]) == 0
    assert twse.streak([]) == 0


# ---------------- scoring ----------------
def test_uptrend_scores_above_downtrend():
    up = scoring.tech_frame(make_df(trend(drift=0.003))).iloc[-1]
    dn = scoring.tech_frame(make_df(trend(drift=-0.003))).iloc[-1]
    for h in scoring.HORIZONS:
        assert up[f"t_{h}"] > 0 > dn[f"t_{h}"]
        assert -1 <= dn[f"t_{h}"] <= up[f"t_{h}"] <= 1


def test_tech_frame_has_no_lookahead():
    """Score at day t must not change when later bars are appended."""
    p = trend(400)
    full = scoring.tech_frame(make_df(p))
    part = scoring.tech_frame(make_df(p[:300]))
    assert full["t_1m"].iloc[299] == pytest.approx(part["t_1m"].iloc[-1])


def test_blend_renormalises_missing_groups():
    assert scoring.blend("1m", {"tech": 0.5, "chips": None, "fund": None, "buzz": None}) == pytest.approx(0.5)
    mixed = scoring.blend("1m", {"tech": 1.0, "chips": -1.0, "fund": None, "buzz": None})
    assert mixed == pytest.approx((0.60 - 0.25) / 0.85)


def test_labels():
    assert [scoring.label(x) for x in (0.6, 0.2, 0, -0.2, -0.6)] == ["強勢偏多", "偏多", "中性", "偏空", "強勢偏空"]


def test_chips_tw_direction_and_margin_warning():
    buy, _ = scoring.chips_tw({"f5": 0.8, "f20": 2.0, "t5": 0.2, "streak": 6})
    sell, notes = scoring.chips_tw({"f5": -0.8, "f20": -2.0, "t5": -0.2, "streak": -6,
                                    "margin_chg5": 0.08, "r5": -0.05})
    assert buy > 0.5 and sell < -0.5
    assert any("融資增" in t for t, _ in notes)
    assert scoring.chips_tw({})[0] is None


def test_chips_us_and_fundamentals():
    assert scoring.chips_us({"congress_net": 4, "guru_net": 2})[0] > 0.4
    assert scoring.chips_us({"congress_net": None, "guru_net": None})[0] is None
    assert scoring.chips_us({"congress_net": 0, "guru_net": 0})[0] == 0
    good, _ = scoring.fund_tw({"rev_yoy": 40, "rev_cum_yoy": 30, "pe": 11, "yield": 5.5, "eps": 3})
    bad, notes = scoring.fund_tw({"rev_yoy": -35, "rev_cum_yoy": -30, "pe": 80, "eps": -1})
    assert good > 0.5 and bad < -0.4
    assert any("虧損" in t for t, _ in notes)
    assert scoring.fund_tw({})[0] is None
    assert scoring.fund_us({"eps_yoy": 60, "rev_yoy": 30, "pe": 22})[0] > 0.4


def test_score_stock_and_trade_plan():
    tf = scoring.tech_frame(make_df(trend(drift=0.003)))
    sc = scoring.score_stock(tf, 0.5, 0.5, 0.1)
    assert sc["overall"] > 0.3 and set(sc["horizons"]) == {"1w", "1m", "1q"}
    plan = scoring.trade_plan(tf.iloc[-1], sc["horizons"]["1m"], sc["overall"])
    close = tf["close"].iloc[-1]
    assert plan["stop"] < plan["support"] <= close < plan["target"]


def test_backtest_runs_and_detects_persistent_trends():
    frames = {f"S{i}": scoring.tech_frame(make_df(trend(900, drift=(i - 15) * 0.0002, seed=i)))
              for i in range(30)}
    bt = scoring.backtest(frames, lookback_days=500)
    assert bt["1m"]["samples"] > 20
    assert bt["1q"]["ic"] > 0  # drifts persist by construction, so the score must rank them


# ---------------- parsers ----------------
def test_twse_number_and_date_parsing():
    assert twse.num("1,234.5") == 1234.5 and twse.num("-34,116,689,906") == -34116689906
    assert twse.num("") is None and twse.num("--") is None and twse.num("2.99%") == 2.99
    assert twse.roc_to_iso("1151109") == "2026-11-09" and twse.roc_to_iso("115/11/02") == "2026-11-02"
    assert twse.roc_to_iso("bad") is None
    assert twse.COMMON_STOCK.match("2330") and not twse.COMMON_STOCK.match("0050")
    assert not twse.COMMON_STOCK.match("00632R")


def test_parse_quiver_and_summary():
    html = ("<script>let recentTradesData = [['NVDA', 'NVIDIA CORP', 'ST', 'Purchase', '$1,001 - $15,000', "
            "'Jane Doe', 'House', 'D', '2026-11-06 00:00:00', '2026-10-20 00:00:00', '-', 'x', 1.5, 'Jane Doe', "
            "'u', 'D1'], ['BRK.B', 'BERKSHIRE', 'ST', 'Sale (Partial)', '$15,001 - $50,000', 'John Roe', "
            "'Senate', 'R', '2026-11-01 00:00:00', '2026-10-01 00:00:00', '-', 'y', null, 'John Roe', 'u', 'R1'], "
            "['NVDA', 'NVIDIA CORP', 'ST', 'Purchase', '$1,001 - $15,000', 'Old Trade', 'House', 'D', "
            "'2026-01-06 00:00:00', '2026-01-02 00:00:00', '-', 'z', 0, 'Old Trade', 'u', 'O1']];</script>")
    trades = us_data.parse_quiver(html)
    assert [t["type"] for t in trades] == ["buy", "sell", "buy"]
    assert trades[1]["ticker"] == "BRK-B"
    s = us_data.congress_summary(trades, dt.date(2026, 11, 10))
    assert s["NVDA"] == {"buys": 1, "sells": 0, "net": 1, "members": ["Jane Doe"]}  # old trade excluded
    assert s["BRK-B"]["net"] == -1
    with pytest.raises(RuntimeError):
        us_data.parse_quiver("<html></html>")


XML_13F = """<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><cusip>037833100</cusip><value>{a}</value>
<shrsOrPrnAmt><sshPrnamt>{ash}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><cusip>037833100</cusip><value>100</value>
<shrsOrPrnAmt><sshPrnamt>10</sshPrnamt></shrsOrPrnAmt><putCall>Put</putCall></infoTable>
{extra}</informationTable>"""
ROW = ("<infoTable><nameOfIssuer>{n}</nameOfIssuer><cusip>{c}</cusip><value>{v}</value>"
       "<shrsOrPrnAmt><sshPrnamt>{s}</sshPrnamt></shrsOrPrnAmt></infoTable>")


def test_13f_parse_and_diff():
    cur = us_data.parse_13f(XML_13F.format(a=900, ash=90, extra=ROW.format(n="NEWCO", c="111111111", v=100, s=5)))
    prev = us_data.parse_13f(XML_13F.format(a=1000, ash=100, extra=ROW.format(n="GONE", c="222222222", v=500, s=9)))
    assert cur["037833100"] == {"name": "APPLE INC", "value": 900.0, "shares": 90.0}  # put option ignored
    d = us_data.diff_13f(cur, prev)
    top = {r["name"]: r for r in d["top"]}
    assert top["APPLE INC"]["action"] == "cut" and top["APPLE INC"]["chg"] == pytest.approx(-0.1)
    assert top["APPLE INC"]["weight"] == pytest.approx(0.9)
    assert top["NEWCO"]["action"] == "new"
    assert d["exits"][0]["name"] == "GONE" and d["actions"]["222222222"] == "exit"


def q(start, end, val, filed="2026-01-01"):
    return {"start": start, "end": end, "val": val, "filed": filed}


def test_ttm_derives_fiscal_q4_and_yoy():
    units = [q("2024-01-01", "2024-03-31", 1.0), q("2024-04-01", "2024-06-30", 1.1),
             q("2024-07-01", "2024-09-30", 1.2), q("2024-01-01", "2024-12-31", 4.6),  # Q4 = 1.3 derived
             q("2025-01-01", "2025-03-31", 1.5), q("2025-04-01", "2025-06-30", 1.65),
             q("2025-01-01", "2025-06-30", 3.15)]  # 6-month YTD fact must be ignored
    r = us_data.ttm_and_yoy(units)
    assert r["period"] == "2025-06-30" and r["latest"] == 1.65
    assert r["ttm"] == pytest.approx(1.2 + 1.3 + 1.5 + 1.65)
    assert r["yoy"] == pytest.approx(50.0)
    assert us_data.ttm_and_yoy([]) == {}


def test_ttm_uses_restated_value_from_latest_filing():
    units = [q("2025-01-01", "2025-03-31", 9.9, filed="2025-05-01"),
             q("2025-01-01", "2025-03-31", 1.0, filed="2026-05-01")]
    assert us_data.ttm_and_yoy(units)["latest"] == 1.0


# ---------------- media ----------------
RSS = """<?xml version="1.0" encoding="UTF-8"?><rss><channel>
<item><title>EP1 聊台積電與 NVDA 財報</title><pubDate>Mon, 09 Nov 2026 10:00:00 +0800</pubDate>
<link>https://example.com/1</link><description><![CDATA[<p>本集談輝達&amp;AMD，https://x.co/abc 以及 MU。</p>]]></description></item>
<item><title>Old</title><pubDate>Mon, 05 Jan 2026 10:00:00 +0800</pubDate><description>台積電</description></item>
</channel></rss>"""


def test_feed_parsing_and_mentions():
    since = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    eps = media.parse_feed(RSS, since)
    assert len(eps) == 1 and eps[0]["date"] == "2026-11-09"
    assert "<p>" not in eps[0]["summary"] and "https://" not in eps[0]["summary"]
    m = media.build_matchers({"2330.TW": ["台積電"], "NVDA": ["NVDA", "輝達"], "AMD": ["AMD"],
                              "MU": ["MU"], "MA": ["MA"], "ARM": ["ARM"]})
    found = media.find_mentions(media.episode_texts(eps)[0], m)
    assert set(found) == {"2330.TW", "NVDA", "AMD", "MU"}
    assert media.find_mentions("the MUSEUM and ARMY are MAD", m) == []  # word boundaries


def test_repeated_sponsor_blocks_are_not_mentions():
    def ep(title, notes):
        return {"title": title, "_chunks": media.chunks(notes)}
    footer = "<p>贊助：AI 財報摘要： Microsoft： Google： Amazon：</p><p>追蹤我們的 IG</p>"
    eps = [ep("EP1 聊 Google 財報", "<p>本集談搜尋廣告。</p>" + footer),
           ep("EP2 航運展望", "<p>長榮與貨櫃運價。</p>" + footer),
           ep("EP3 雲端資本支出", "<p>Amazon 的 AWS 成長加速。</p>" + footer)]
    m = media.build_matchers({"GOOGL": ["Google"], "AMZN": ["Amazon"], "MSFT": ["Microsoft"], "2603": ["長榮"]})
    got = [set(media.find_mentions(t, m)) for t in media.episode_texts(eps)]
    assert got == [{"GOOGL"}, {"2603"}, {"AMZN"}]


def test_ttm_derives_q4_for_consecutive_fiscal_years():
    units = []
    for year, vals, fy in ((2024, (1.0, 1.1, 1.2), 4.6), (2025, (1.5, 1.6, 1.7), 6.6)):
        for (s, e), v in zip((("01-01", "03-31"), ("04-01", "06-30"), ("07-01", "09-30")), vals):
            units.append(q(f"{year}-{s}", f"{year}-{e}", v))
        units.append(q(f"{year}-01-01", f"{year}-12-31", fy))
    r = us_data.ttm_and_yoy(units)
    assert r["period"] == "2025-12-31" and r["latest"] == pytest.approx(1.8)   # 6.6 - 4.8
    assert r["ttm"] == pytest.approx(6.6)
    assert r["yoy"] == pytest.approx((1.8 - 1.3) / 1.3 * 100)                   # vs derived 2024 Q4


# ---------------- price store ----------------
def test_compact_splits_on_week_boundary_and_stays_ordered():
    from pipeline import prices
    idx = pd.bdate_range("2015-01-01", "2026-11-10")
    c = pd.Series(np.linspace(10, 100, len(idx)), index=idx)
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 100.0})
    out = prices.compact(df, daily_years=5)
    dates = [r[0] for r in out["w"]] + [r[0] for r in out["d"]]
    assert dates == sorted(set(dates))                     # strictly increasing across the w/d seam
    assert pd.Timestamp(out["d"][0][0]).weekday() == 0     # daily part starts on a Monday
    assert out["w"][0][5] == 200 and out["w"][1][5] == 500  # 2 bars in the first week, then full weeks
    # carrying older weekly bars forward keeps them, without duplicating the overlap
    again = prices.compact(prices.rows_to_frame(out["d"]), 5, out["w"])
    assert again["w"] == out["w"] and again["d"] == out["d"]


def test_merge_prefers_new_rows_and_append_session():
    from pipeline import prices
    old = prices.rows_to_frame([["2026-11-05", 1, 2, 1, 2, 10], ["2026-11-06", 2, 3, 2, 3, 10]])
    new = prices.rows_to_frame([["2026-11-06", 2, 4, 2, 4, 20], ["2026-11-09", 4, 5, 4, 5, 30]])
    m = prices.merge(old, new)
    assert list(m["close"]) == [2, 4, 5] and m.index.is_monotonic_increasing
    raw = [{"datetime": "2026-11-10T09:31:00", "price": {"open": 5, "high": 6, "low": 5, "close": 6},
            "volume": {"stock_volume": 7}},
           {"datetime": "2026-11-10T09:32:00", "price": {"open": 6, "high": 8, "low": 6, "close": 7},
            "volume": {"stock_volume": 3}}]
    import datetime
    assert not prices.session_complete(raw, now=datetime.datetime(2026, 11, 10, 9, 40))   # still trading
    assert prices.session_complete(raw, now=datetime.datetime(2026, 11, 11, 8, 0))        # next morning
    closing = [{**raw[1], "datetime": "2026-11-10T15:59:00"}]
    assert prices.session_complete(closing, now=datetime.datetime(2026, 11, 10, 16, 5))
    after = datetime.datetime(2026, 11, 11, 8, 0)
    assert len(prices.append_session(m, raw, now=datetime.datetime(2026, 11, 10, 9, 40))) == len(m)  # unfinished
    out = prices.append_session(m, raw, now=after)
    assert out.index[-1] == pd.Timestamp("2026-11-10")
    assert list(out.iloc[-1]) == [5, 8, 5, 7, 10]
    assert len(prices.append_session(out, raw, now=after)) == len(out)  # already present -> unchanged
    assert prices.intraday_5m(raw)[0][1:] == [5, 8, 5, 7, 10]


def test_yahoo_only_fills_older_history():
    from pipeline import build, prices
    official = prices.rows_to_frame([["2026-11-09", 10, 11, 9, 10, 5], ["2026-11-10", 10, 12, 10, 12, 6]])
    yahoo = prices.rows_to_frame([["2026-11-01", 1, 1, 1, 1, 1],   # a Sunday: bogus row
                                  ["2026-11-06", 8, 9, 8, 9, 4], ["2026-11-09", 99, 99, 99, 99, 9],
                                  ["2026-11-11", 50, 50, 50, 50, 1]])
    out = build.with_older_history(official, yahoo)
    assert list(out.index.strftime("%m-%d")) == ["11-01", "11-06", "11-09", "11-10"]
    assert out.loc["2026-11-09", "close"] == 10           # official bar wins, later Yahoo rows ignored
    seeded = build.with_older_history(None, yahoo)
    assert "2026-11-01" not in seeded.index.strftime("%Y-%m-%d")  # weekend row dropped when seeding


def test_13f_values_reported_in_thousands_are_rescaled():
    rows = "".join(ROW.format(n=f"CO{i}", c=f"{i:09d}", v=50 * (i + 1), s=1000 * (i + 1)) for i in range(5))
    table = us_data.parse_13f(f"<informationTable>{rows}</informationTable>")   # $0.05 a share -> thousands
    assert table["000000000"]["value"] == 50_000
    rows = "".join(ROW.format(n=f"CO{i}", c=f"{i:09d}", v=50_000 * (i + 1), s=1000 * (i + 1)) for i in range(5))
    assert us_data.parse_13f(f"<informationTable>{rows}</informationTable>")["000000000"]["value"] == 50_000


def test_monthly_bars_are_not_accepted_as_daily_history():
    from pipeline import prices
    daily = make_df(trend(300))
    monthly = daily.resample("MS").first().dropna()
    assert prices.is_daily(daily) and not prices.is_daily(monthly)
    assert not prices.is_daily(pd.concat([monthly.iloc[:40], daily.iloc[-40:]]).sort_index())


def test_fetch_days_backs_off_and_stops_when_throttled(monkeypatch):
    monkeypatch.setattr(twse.time, "sleep", lambda s: None)
    monkeypatch.setattr(twse, "THROTTLED", False)
    calls = []

    def flaky(d):
        calls.append(d)
        if d == "d2":
            raise twse.NoData("holiday")
        if d == "d3" and calls.count("d3") == 1:
            raise RuntimeError("HTTP 307")
        return d.upper()

    assert twse.fetch_days(["d1", "d2", "d3"], flaky, log=lambda m: None) == {"d1": "D1", "d3": "D3"}
    assert calls == ["d1", "d2", "d3", "d3"] and twse.THROTTLED is False

    def blocked(d):
        calls.append(d)
        raise RuntimeError("HTTP 307")

    calls.clear()
    assert twse.fetch_days([f"x{i}" for i in range(10)], blocked, log=lambda m: None) == {}
    assert len(calls) == 6 and twse.THROTTLED is True      # 3 refusals per pass, then give up
    calls.clear()
    assert twse.fetch_days(["y1"], blocked, log=lambda m: None) == {} and calls == []  # no more requests


# ---------------- ETFs, podcast-hot, weekly AI file ----------------
def test_etf_category_and_record():
    from pipeline import build
    assert build.etf_category("00631L", "元大台灣50正2") == "槓桿型"
    assert build.etf_category("00632R", "元大台灣50反1") == "反向型"
    assert build.etf_category("00878", "國泰永續高股息") == "高股息"
    assert build.etf_category("00891", "中信關鍵半導體") == "產業主題"
    assert build.etf_category("00981A", "主動統一台股增長") == "主動式"
    assert build.etf_category("0050", "元大台灣50") == "市值型"
    assert build.etf_category("009816", "凱基台灣TOP50") == "市值型"
    assert build.etf_category("00406A", "主動中信台灣收益") == "主動式"   # active beats the income keyword
    df = make_df(trend(drift=0.003))
    meta = {"code": "00631L", "name": "元大台灣50正2", "market": "TW", "industry": "槓桿型"}
    flows = {"00631L": [(2e6, 1e5)] * 20}
    rec = build.etf_record("00631L.TW", df, scoring.tech_frame(df), meta, flows, 2)
    assert rec["kind"] == "etf" and rec["overall"] > 0.3 and rec["factors"]["fund"] is None
    assert rec["foreign5"] == pytest.approx(10000) and rec["factors"]["chips"] > 0
    assert any("槓桿" in t for t in rec["risks"]) and -1 <= rec["mdd"] <= 0
    us = build.etf_record("SPY", df, scoring.tech_frame(df), {**meta, "market": "US", "industry": "市值型"}, flows, 0)
    assert us["factors"]["chips"] is None and "foreign5" not in us


def test_hot_view_reads_attention_against_the_data():
    from pipeline import build
    assert "同向偏多" in build.hot_view({"overall": 0.4, "rsi": 55, "chg5d": 0.01}, 3)
    assert "追高風險" in build.hot_view({"overall": 0.4, "rsi": 80, "chg5d": 0.12}, 3)
    assert "利多不漲" in build.hot_view({"overall": -0.4, "rsi": 45, "chg5d": -0.01}, 3)
    assert "方向未明" in build.hot_view({"overall": 0.0, "rsi": None, "chg5d": None}, 1)


def test_check_ai_validation():
    from pipeline import check_ai
    good = {"data_date": "2026-11-10",
            "market": "".join(f"### 段落{i}\n" + "內容" * 40 + "\n" for i in range(4)),
            "podcast": {"overview": "概況" * 40, "stocks": {"NVDA": "分析" * 30, "2330.TW": "分析" * 30}},
            "etf": "ETF" * 60}
    hot = ["NVDA", "2330.TW"]
    assert check_ai.validate(good, "2026-11-10", hot) == []
    assert any("data_date" in e for e in check_ai.validate(good, "2026-11-17", hot))          # stale commentary
    assert any("lacks" in e for e in check_ai.validate(good, "2026-11-10", hot + ["AMD"]))    # hot name skipped
    bad = {**good, "podcast": {"overview": good["podcast"]["overview"],
                               "stocks": {**good["podcast"]["stocks"], "TSLA": "分析" * 30}}}
    assert any("not in this week" in e for e in check_ai.validate(bad, "2026-11-10", hot))
    assert any("sections" in e for e in check_ai.validate({**good, "market": "短" * 300}, "2026-11-10", hot))
    assert any("placeholder" in e for e in check_ai.validate({**good, "etf": "TODO " * 40}, "2026-11-10", hot))
    assert check_ai.validate({**good, "podcast": {}}, "2026-11-10", []) == []                  # quiet week
    assert any("fund" in e for e in check_ai.validate(good, "2026-11-10", hot, need_fund=True))  # funds built, no text
    assert check_ai.validate({**good, "fund": "基金" * 80}, "2026-11-10", hot, need_fund=True) == []


# ---------------- mutual funds ----------------
def nav_from(prices, start="2021-01-04"):
    return pd.Series(prices, index=pd.bdate_range(start, periods=len(prices)), dtype=float)


def test_fund_frac_and_nav_series():
    from pipeline import funds
    assert funds.frac(12.5) == 0.125 and funds.frac("-3") == -0.03
    assert funds.frac(None) is None and funds.frac("n/a") is None and funds.frac(999999.99) is None
    s = funds.nav_series({"nav": [10.5, 10.4, 0, 10.0], "tradeDate": [1794240000, 1794153600, 1794067200, 1793894400]})
    assert list(s) == [10.0, 10.4, 10.5] and s.index.is_monotonic_increasing     # ascending, zero dropped
    assert funds.annualise(0.331, 3) == pytest.approx(0.1, abs=1e-3) and funds.annualise(None, 3) is None


def test_fund_risk_metrics():
    from pipeline import funds
    steady = nav_from(100 * np.exp(np.cumsum(np.full(900, 0.0004))))
    m = funds.risk_metrics(steady, None, 0.015)
    assert m["vol_3y"] < 0.001 and m["mdd_3y"] == pytest.approx(0, abs=1e-9) and m["win_months"] == 1.0
    crash = nav_from(list(np.linspace(100, 150, 500)) + list(np.linspace(150, 90, 100)) + list(np.linspace(90, 120, 300)))
    c = funds.risk_metrics(crash, 0.08, 0.015)
    assert c["mdd_3y"] == pytest.approx(-0.4, abs=0.01) and c["mdd_1y"] <= 0
    assert c["sharpe"] == pytest.approx((0.08 - 0.015) / c["vol_3y"])
    assert 2.9 < c["span_years"] <= 3.01
    assert funds.risk_metrics(nav_from([10] * 20), None, 0) == {}                     # too short


def test_fund_performance_score_and_grades():
    from pipeline import funds
    top = funds.performance_score({"1y": 90, "3y": 95, "5y": 85}, 0.06, 1.4, -0.12)
    bottom = funds.performance_score({"1y": 10, "3y": 5}, -0.07, -0.2, -0.45)
    assert top[0] > 75 and top[1] == "優異" and bottom[0] < 30 and bottom[1] == "落後"
    assert top[2]["peer"] == pytest.approx(0.3 * 90 + 0.45 * 95 + 0.25 * 85)
    assert funds.performance_score({"3y": 50}, 0.0, 0.5, -0.25)[0] == pytest.approx(50.0)   # all midpoints
    # missing pieces are dropped and the rest re-weighted; a single number is not an evaluation
    partial = funds.performance_score({}, None, 0.5, -0.25)
    assert partial[0] == pytest.approx(50.0) and partial[2]["peer"] is None
    assert funds.performance_score({"1y": 99}, None, None, None)[:2] == (None, None)


def test_fund_build_record_and_verdict():
    from pipeline import funds
    idx = pd.bdate_range("2022-01-03", periods=1000)
    navs = 10 * np.exp(np.cumsum(np.random.default_rng(3).normal(0.0006, 0.01, 1000)))
    items = {"nav": list(navs[::-1]), "tradeDate": [int(t.timestamp()) for t in idx[::-1]],
             "displayNameLocal": "測試台灣科技基金", "categoryLocal": "台灣大型股票", "classCurrencyLocal": "新台幣",
             "riskLevel": 4, "starRating": 5, "managementFee": 1.6, "dividendDistributionFrequency": "",
             "inceptionDate": 946684800, "totalNetAsset": 5e10, "netAssetCurrency": "TWD",
             "netAssetDate": 1790697600, "primaryProspectusIndexName": "TAIEX", "return1YearLocal": 42.0}
    perf = {"returns": {"return1Month": -5.0, "return1Year": 40.0, "accReturn3Year": 120.0},
            "indexReturns": {"return1Year": 30.0, "accReturn3Year": 90.0},
            "categoryAverages": {"return1Year": 25.0},
            "categoryRankings": {"return1Year": {"ranking": 5, "total": 100, "surpassRate": 95},
                                 "accReturn3Year": {"ranking": 10, "total": 90, "surpassRate": 89},
                                 "accReturn5Year": {"ranking": None, "total": 0, "surpassRate": None}}}
    rec, nav = funds.build_record("A00001", "台股", items, perf, {"新台幣": 1.5, "default": 2.0})
    assert rec["symbol"] == "F-A00001" and rec["kind"] == "fund" and len(nav) == 1000
    assert rec["ret"]["1y"] == 0.40 and rec["ret"]["3y"] == 1.20 and rec["ret"]["5y"] is None
    assert rec["beat"] == {"1y": 95, "3y": 89} and "5y" not in rec["rank"]
    assert rec["excess_basis"] == "3y"
    assert rec["excess_ann"] == pytest.approx(2.2 ** (1 / 3) - 1.9 ** (1 / 3))
    assert rec["sharpe"] == pytest.approx((2.2 ** (1 / 3) - 1 - 0.015) / rec["vol_3y"])
    assert rec["grade"] in ("優異", "良好") and rec["aum"] == 5e10 and rec["ret_1y_twd"] == 0.42
    assert "贏過 89% 的同類基金" in rec["verdict"] and "高於對比指數" in rec["verdict"]
    young, _ = funds.build_record("A2", "台股", {**items, "nav": items["nav"][:150], "tradeDate": items["tradeDate"][:150]},
                                  perf, {"default": 2.0})
    assert young["score"] is None and young["grade"] is None            # under a year of history: no grade
    assert "配息級別" in funds.verdict({"beat": {"1y": 20}, "dist": "Monthly", "mdd_3y": -0.3})
    assert funds.verdict({}) == "成立時間或資料不足，暫無法評估。"


# ---------------- margin maintenance ----------------
def test_maintenance_ratio_estimate():
    margin = {"2330": {"margin": 1000}, "2317": {"margin": 2000}, "0050": {"margin": 500}}
    closes = {"2330": 1000.0, "2317": 200.0, "0050": 100.0}
    # collateral = 1000*1000*1000 + 2000*1000*200 + 500*1000*100 = 1.45e9 against 1e9 lent
    m = twse.maintenance_ratio(margin, closes, 1e9)
    assert m["maint"] == pytest.approx(1.45) and m["collateral"] == pytest.approx(14.5) and m["coverage"] == 1
    # a small unpriced position is tolerated, a large one makes the estimate untrustworthy
    assert twse.maintenance_ratio({**margin, "9999": {"margin": 100}}, closes, 1e9)["maint"] == pytest.approx(1.45)
    assert twse.maintenance_ratio({**margin, "9999": {"margin": 5000}}, closes, 1e9) is None
    assert twse.maintenance_ratio(margin, closes, 0) is None and twse.maintenance_ratio({}, closes, 1e9) is None


def test_maintenance_status_and_cushion():
    assert [twse.maint_status(x) for x in (1.70, 1.55, 1.45, 1.35, None)] == ["充裕", "正常", "偏低", "斷頭壓力大", "無資料"]
    assert twse.MAINT_INITIAL == pytest.approx(1.6667, abs=1e-4)
    assert 1 - twse.MAINT_CALL / 1.625 == pytest.approx(0.20)   # at 162.5% a 20% fall reaches the call line


def test_margin_extra_gauges():
    from pipeline import build
    assert build.margin_extra({"margin": 500, "limit": 2000, "short": 50}) == {"usage": 0.25, "short_ratio": 0.1}
    assert build.margin_extra({"margin": 0, "limit": None, "short": 5}) == {"usage": None, "short_ratio": None}


# ---------------- leading indicators ----------------
def test_retail_futures_position():
    from pipeline import leading
    inst = [{"ContractCode": "小型臺指期貨", "OpenInterest(Net)": "-8933"}, {"ContractCode": "小型臺指期貨", "OpenInterest(Net)": "-39"},
            {"ContractCode": "小型臺指期貨", "OpenInterest(Net)": "3256"}, {"ContractCode": "臺股期貨", "OpenInterest(Net)": "-99999"}]
    fut = [{"Contract": "MTX", "TradingSession": "一般", "OpenInterest": "30000"},
           {"Contract": "MTX", "TradingSession": "一般", "OpenInterest": "10000"},
           {"Contract": "MTX", "TradingSession": "盤後", "OpenInterest": "-"},      # after-hours rows carry no OI
           {"Contract": "TX", "TradingSession": "一般", "OpenInterest": "122272"}]
    r = leading.retail_position(inst, fut, "小型臺指期貨", "MTX")
    assert r["net"] == 5716 and r["oi"] == 40000 and r["ratio"] == pytest.approx(0.1429, abs=1e-4)   # retail net long
    assert leading.retail_position(inst, fut, "微型臺指期貨", "TMF") is None


def test_adr_premium_percentile_and_term_structure():
    from pipeline import leading
    idx = pd.bdate_range("2026-01-05", periods=40)
    adr = pd.Series(110.0, index=idx)
    tw = pd.Series(600.0, index=idx.delete(3))            # Taiwan closed one day
    fx = pd.Series(30.0, index=idx[:30])                  # FX series lags: carried forward
    prem = leading.adr_premium(adr, tw, fx)
    assert len(prem) == 39 and prem.iloc[-1] == pytest.approx(110 * 30 / (5 * 600) - 1)   # +10%
    assert leading.percentile(pd.Series(range(100))) == 1.0 and leading.percentile(pd.Series([1.0] * 5)) is None
    ts = leading.term_structure(pd.Series([30.0, 15.0], index=idx[:2]), pd.Series([25.0, 20.0], index=idx[:2]))
    assert list(ts) == [1.2, 0.75]


def test_breadth_counts_only_stocks_with_an_average():
    from pipeline import leading
    idx = pd.bdate_range("2026-01-05", periods=3)
    def f(close, ma20, ma60):
        return pd.DataFrame({"close": close, "ma20": ma20, "ma60": ma60}, index=idx)
    frames = {f"U{i}": f(10.0, 9.0, 9.0) for i in range(8)}                     # above both
    frames.update({f"D{i}": f(10.0, 11.0, 9.0) for i in range(4)})              # below 20MA only
    frames["N"] = f(10.0, float("nan"), float("nan"))                            # too new: not counted
    b = leading.breadth(frames)
    assert b["above20"].iloc[-1] == pytest.approx(8 / 12) and b["above60"].iloc[-1] == 1.0


def test_revenue_momentum_crossover():
    from pipeline import leading
    months = {f"2025-{m:02d}": 100.0 for m in range(1, 13)}
    flat = leading.revenue_momentum({**months, "2026-01": 100.0})
    assert flat["ratio"] == 1 and not flat["cross_up"] and not flat["cross_down"]
    up = leading.revenue_momentum({**months, "2026-01": 160.0})
    assert up["ratio"] > 1 and up["cross_up"] and up["rising"] and up["month"] == "2026-01"
    down = leading.revenue_momentum({**months, "2026-01": 40.0})
    assert down["cross_down"] and not down["rising"]
    assert leading.revenue_momentum({"2026-01": 1.0}) is None
    assert leading.revenue_momentum({**months, "2025-06": 0.0}) is None            # a zero month is not usable
    good, notes = scoring.fund_tw({"rev_yoy": 5, "pe": 18, "rev_ratio": 1.2, "rev_cross_up": True})
    base, _ = scoring.fund_tw({"rev_yoy": 5, "pe": 18})
    assert good == pytest.approx(base + 0.10) and any("站上 12 月均線" in t for t, _ in notes)


def test_parse_mops_revenue_table():
    from pipeline import leading
    html = ("<table><tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th></tr>"
            "<tr><td>1101</td><td>台泥</td><td align=right> 14,321,556</td><td>1</td></tr>"
            "<tr><td>合計</td><td></td><td>99</td></tr><tr><td>2330</td><td>台積電</td><td>&nbsp;511,857,399</td></tr></table>")
    assert leading.parse_mops_revenue(html) == {"1101": 14321556.0, "2330": 511857399.0}


def test_cohort_results_against_benchmark():
    from pipeline import leading
    idx = pd.bdate_range("2026-11-02", periods=8)
    mk = lambda vals: pd.DataFrame({"close": vals}, index=idx)   # noqa: E731
    frames = {"A": mk([100, 100, 100, 100, 110, 110, 110, 121]), "B": mk([50] * 7 + [45]),
              "TWII": mk([1000] * 7 + [1050])}
    cohorts = [{"date": "2026-11-05", "picks": [{"symbol": "A", "market": "TW", "close": 100},
                                                {"symbol": "B", "market": "TW", "close": 50},
                                                {"symbol": "GONE", "market": "TW", "close": 9}]}]
    r = leading.cohort_results(cohorts, frames, {"TW": "TWII"})[0]["TW"]
    assert r["n"] == 2 and r["ret"] == pytest.approx((0.21 - 0.10) / 2) and r["bench"] == pytest.approx(0.05)
    assert r["excess"] == pytest.approx(0.005) and r["win"] == 0.5 and r["days"] == 4
    assert leading.cohort_results([{"date": "2026-11-11", "picks": cohorts[0]["picks"]}], frames, {"TW": "TWII"}) == []


def test_attention_signal_and_sbl_moves():
    from pipeline import leading
    assert "法人先行" in leading.attention_signal(1, 1, 0.4, -0.01)
    assert "過熱" in leading.attention_signal(4, 3, -0.5, 0.12)
    assert leading.attention_signal(1, 1, 0.0, 0.0) is None and leading.attention_signal(2, 1, None, 0.2) is None
    now = {"2330": {"name": "台積電", "sbl": 2_000_000, "short": 100_000}, "0050": {"name": "ETF", "sbl": 9e9, "short": 1},
           "1101": {"name": "台泥", "sbl": 1000, "short": 0}}
    ref = {"2330": {"name": "台積電", "sbl": 1_000_000, "short": 100_000}, "0050": {"name": "ETF", "sbl": 1e9, "short": 1},
           "1101": {"name": "台泥", "sbl": 500, "short": 0}}
    rows = leading.sbl_moves(now, ref, {"2330": 2000.0, "0050": 100.0, "1101": 30.0})
    assert [r["code"] for r in rows] == ["2330"]                 # ETF code and tiny positions are left out
    assert rows[0]["chg"] == 1.0 and rows[0]["short_chg"] == 0 and rows[0]["value"] == pytest.approx(40.0)


def test_leading_card_accepts_series_and_lists():
    from pipeline import build
    c = build._card("k", "名稱", "1.0", "+0.1", "說明", 1, pd.Series([1.23456, 2.0]))
    assert c["spark"] == [1.2346, 2.0] and build._card("k", "n", "v", "c", "r")["spark"] == []


# ---------------- second batch of indicators ----------------
def test_option_expiry_dates_and_max_pain():
    from pipeline import leading
    assert leading.expiry_date("202611") == dt.date(2026, 11, 18)        # third Wednesday
    assert leading.expiry_date("202611W2") == dt.date(2026, 11, 11)
    assert leading.expiry_date("202611F2") == dt.date(2026, 11, 13)
    assert leading.expiry_date("bad") is None and leading.expiry_date("202613") is None
    # holders of 100 calls struck at 90 and 100 puts struck at 110 collect least at 100
    assert leading.max_pain({90.0: (100, 0), 100.0: (10, 10), 110.0: (0, 100)}) == 100.0
    assert leading.max_pain({}) is None


def test_option_walls_pick_live_wednesday_expiry():
    from pipeline import leading
    def row(code, strike, cp, oi, session="一般", contract="TXO"):
        return {"Contract": contract, "ContractMonth(Week)": code, "StrikePrice": str(strike), "CallPut": cp,
                "OpenInterest": str(oi), "TradingSession": session}
    rows = [row("202611W2", 100, "買權", 999),                                   # expires on the build day: skipped
            row("202611F2", 100, "買權", 999),                                   # Friday weekly: skipped
            row("202611", 90, "賣權", 500), row("202611", 95, "賣權", 300), row("202611", 100, "買權", 50),
            row("202611", 105, "買權", 400), row("202611", 110, "買權", 700), row("202611", 80, "買權", 900),
            row("202611", 110, "買權", 5000, session="盤後"), row("202611", 110, "買權", 5000, contract="TEO")]
    walls = leading.option_walls(rows, dt.date(2026, 11, 11), spot=100.0)
    assert len(walls) == 1 and walls[0]["code"] == "202611" and walls[0]["monthly"]
    w = walls[0]
    assert w["call_wall"] == 110.0      # the deep in-the-money 80 call is below the index and does not count
    assert w["put_wall"] == 90.0 and w["call_oi"] == 2050 and w["put_oi"] == 800
    assert leading.trim_ladder(w["ladder"], 2) == [[80.0, 900.0, 0.0], [110.0, 700.0, 0.0]]
    assert leading.option_walls(rows, dt.date(2026, 12, 1), spot=100.0) == []


def test_large_traders_and_futures_basis():
    from pipeline import leading
    rows = [{"Date": "20261110", "Contract": "TX", "SettlementMonth": "202611", "TypeOfTraders": "0",
             "Top10Buy": "10", "Top10Sell": "5", "OIOfMarket": "100"},
            {"Date": "20261110", "Contract": "TX", "SettlementMonth": "999999", "TypeOfTraders": "0",
             "Top10Buy": "300", "Top10Sell": "500", "OIOfMarket": "1000"},
            {"Date": "20261110", "Contract": "TX", "SettlementMonth": "999999", "TypeOfTraders": "1",
             "Top10Buy": "900", "Top10Sell": "0", "OIOfMarket": "1000"}]
    lt = leading.large_traders(rows)
    assert lt == {"date": "2026-11-10", "net": -200.0, "oi": 1000.0, "ratio": -0.2}
    assert leading.large_traders(rows[:1])["net"] == 5.0 and leading.large_traders([]) is None
    fut = [{"Date": "20261110", "Contract": "TX", "ContractMonth(Week)": "202612", "SettlementPrice": "43000", "TradingSession": "一般"},
           {"Date": "20261110", "Contract": "TX", "ContractMonth(Week)": "202611", "SettlementPrice": "42646", "TradingSession": "一般"},
           {"Date": "20261110", "Contract": "TX", "ContractMonth(Week)": "202611", "SettlementPrice": "NULL", "TradingSession": "盤後"},
           {"Date": "20261110", "Contract": "TX", "ContractMonth(Week)": "202611/202612", "SettlementPrice": "350", "TradingSession": "一般"}]
    assert leading.futures_basis(fut) == {"date": "2026-11-10", "month": "202611", "settle": 42646.0}
    assert leading.futures_basis([]) is None


def test_trust_first_buys_need_a_quiet_spell_then_a_streak():
    from pipeline import leading
    quiet = [(0, 0)] * 12
    flows = {"1111": quiet + [(5000, 300_000), (-1000, 400_000)],                 # 2-day streak after 12 quiet days
             "2222": [(0, 100_000)] + quiet + [(0, 300_000), (0, 400_000)],       # bought earlier in the window
             "3333": quiet + [(0, 300_000)],                                      # one day is not a streak
             "4444": quiet + [(0, 1000), (0, 1000)],                              # too small
             "5555": [(0, -50_000)] * 12 + [(0, 300_000), (0, 400_000)]}          # selling before counts as quiet
    hits = leading.trust_first_buys(flows, {c: 100.0 for c in flows})
    assert [h["code"] for h in hits] == ["1111", "5555"]
    assert hits[0]["streak"] == 2 and hits[0]["quiet"] == 12 and hits[0]["value"] == pytest.approx(0.7)
    assert hits[0]["foreign_value"] == pytest.approx(0.004)
    assert leading.trust_first_buys(flows, {}) == []


def test_margin_cost_rolls_forward_and_gives_a_maintenance_ratio():
    from pipeline import leading
    assert leading.roll_margin_cost(None, 0, 100, 50.0) == 50.0                   # first sight: seeded at the close
    assert leading.roll_margin_cost(50.0, 100, 200, 60.0) == pytest.approx(55.0)  # 100 new lots at 60
    assert leading.roll_margin_cost(55.0, 200, 120, 40.0) == pytest.approx(55.0)  # lots leaving keep the average
    assert leading.roll_margin_cost(55.0, 200, 0, 40.0) == 55.0
    assert leading.stock_maintenance(100.0, 100.0) == pytest.approx(1 / 0.6)      # bought today: the initial 166.7%
    assert leading.stock_maintenance(100.0, 78.0) == pytest.approx(1.30)          # a 22% fall reaches the call line
    assert leading.stock_maintenance(None, 78.0) is None


def test_expected_quarter_and_liability_growth():
    from pipeline import leading
    assert leading.expected_quarter(dt.date(2026, 11, 11)) == "2026-06-30"       # Q3 is due on 11/14
    assert leading.expected_quarter(dt.date(2026, 11, 20)) == "2026-09-30"
    assert leading.expected_quarter(dt.date(2026, 2, 1)) == "2025-09-30"         # the annual report has until end of March
    assert leading.expected_quarter(dt.date(2026, 4, 5)) == "2025-12-31"
    q = {"2025-06-30": {"value": 80.0}, "2025-09-30": {"value": 100.0}, "2025-12-31": {"value": 90.0},
         "2026-03-31": {"value": 100.0}, "2026-06-30": {"value": 130.0, "share": 35.0}, "2026-09-30": {}}
    g = leading.liability_growth(q)
    assert g["quarter"] == "2026-06-30" and g["qoq"] == pytest.approx(0.3) and g["yoy"] == pytest.approx(0.625)
    assert g["share"] == 35.0
    assert leading.liability_growth({"2026-06-30": {"value": 1.0}}) is None


def test_risk_regime_states():
    from pipeline import leading
    idx = pd.bdate_range("2026-01-01", periods=80)
    flat, rising, falling = pd.Series(1.0, index=idx), pd.Series(np.linspace(1, 2, 80), index=idx), pd.Series(np.linspace(2, 1, 80), index=idx)
    tight, wide = pd.Series(3.0, index=idx), pd.Series(4.5, index=idx)
    jump = pd.Series([3.0] * 70 + [3.7] * 10, index=idx)
    assert leading.risk_regime(tight, rising)["state"] == "risk_on"
    assert leading.risk_regime(wide, falling)["state"] == "risk_off"
    assert leading.risk_regime(jump, falling)["state"] == "risk_off"             # tight level, but widening fast
    assert leading.risk_regime(tight, falling)["state"] == "mixed"               # rotation, not a credit problem
    assert leading.risk_regime(wide, rising)["state"] == "mixed"
    assert leading.risk_regime(tight.head(5), flat) is None


def test_advance_decline_counts_common_stocks_only():
    from pipeline import leading
    before = {f"{1000 + i}": 10.0 for i in range(300)}
    now = {c: (11.0 if i < 180 else 9.0 if i < 280 else 10.0) for i, c in enumerate(before)}
    assert leading.advance_decline({**now, "0050": 99.0}, {**before, "0050": 1.0}) == (180, 100)
    assert leading.advance_decline({"1101": 2.0}, {"1101": 1.0}) is None         # too few names to mean anything


def test_trade_plan_sizes_positions_by_volatility():
    calm, wild = make_df(trend(300, 0.001, seed=3)), make_df(trend(300, 0.001, seed=3))
    wild["high"], wild["low"] = wild["close"] * 1.05, wild["close"] * 0.95
    plans = []
    for df in (calm, wild):
        tf = scoring.tech_frame(df)
        sc = scoring.score_stock(tf, None, None, None)
        plans.append(scoring.trade_plan(tf.iloc[-1], sc["horizons"]["1m"], sc["overall"]))
    a, b = plans
    assert b["atr_pct"] > a["atr_pct"] and b["stop_pct"] > a["stop_pct"] and b["size"] < a["size"]
    for p in plans:
        assert p["size"] == pytest.approx(min(0.25, 0.01 / p["stop_pct"]), abs=1e-3)
        assert p["stop"] < p["support"] <= p["zone_high"]


def test_calibrate_costs_matches_market_ratio_then_respects_call_line():
    from pipeline import leading
    book = {"1111": [100.0, 1000], "2222": [50.0, 1000], "3333": [200.0, 10]}
    closes = {"1111": 100.0, "2222": 60.0, "3333": 100.0}
    leading.calibrate_costs(book, closes, market_maint=1.60)
    value = sum(v[1] * closes[c] for c, v in book.items())
    loan = sum(v[1] * v[0] * 0.6 for v in book.values())
    # 3333 was far under water: it is pulled up to the call line, which lifts the total a little above 160%
    assert leading.stock_maintenance(book["3333"][0], 100.0) == pytest.approx(1.30)
    assert 1.60 <= value / loan < 1.62
    assert book["2222"][0] < book["1111"][0]                                      # relative order is kept
    untouched = {"1111": [100.0, 1000]}
    leading.calibrate_costs(untouched, {"1111": 100.0}, None)                     # no market figure: only the floor applies
    assert untouched["1111"][0] == 100.0


# ---------------- market safety ----------------
def test_lin_maps_safe_to_zero_and_danger_to_hundred_either_direction():
    assert market.lin(14, 14, 32) == 0 and market.lin(32, 14, 32) == 100 and market.lin(50, 14, 32) == 100
    assert market.lin(1.70, 1.70, 1.40) == 0 and market.lin(1.55, 1.70, 1.40) == pytest.approx(50)


def test_parse_futures_csv_keeps_foreign_tx_rows_only():
    text = ("日期,商品名稱,身份別,a,b,c,d,多空交易口數淨額,e,f,g,h,i,多空未平倉口數淨額,j\n"
            "2026/10/01,臺股期貨,投信,1,1,1,1,776,1,1,1,1,1,74615,1\n"
            "2026/10/01,臺股期貨,外資及陸資,1,1,1,1,-1389,1,1,1,1,1,-79554,1\n"
            "2026/10/01,小型臺指期貨,外資及陸資,1,1,1,1,5,1,1,1,1,1,9,1\n")
    assert market.parse_futures_csv(text) == {"2026-10-01": {"net_oi": -79554.0, "net_trade": -1389.0}}


def test_breadth_index_and_breakout_check():
    idx = pd.bdate_range("2024-01-01", periods=40)
    b = pd.DataFrame({"above20": 0.8, "above60": 0.6}, index=idx)
    bi = market.breadth_index(b)
    assert bi.iloc[0] == 70 and market.breadth_index(b, ad_ratio=0.6).iloc[-1] == pytest.approx(56)
    rising = pd.Series(np.linspace(100, 140, 40), index=idx)
    assert market.breakout_check(rising, bi)["kind"] == "confirmed"
    thin = pd.Series(np.linspace(80, 35, 40), index=idx)  # breadth falling while the index makes highs
    assert market.breakout_check(rising, thin)["kind"] == "false_risk"
    falling = pd.Series(np.linspace(140, 100, 40), index=idx)
    assert market.breakout_check(falling, pd.Series(15.0, index=idx))["kind"] == "washout"
    assert market.breakout_check(falling, pd.Series(np.linspace(30, 45, 40), index=idx))["kind"] == "divergence_up"


def test_regime_states_follow_the_averages_and_need_confirmation():
    up = pd.Series(np.linspace(100, 300, 400), index=pd.bdate_range("2022-01-03", periods=400))
    st = market.regime_states(up)
    assert st.iloc[-1] == "bull" and len(st) == 400 - 199
    crash = pd.concat([up, pd.Series(np.linspace(295, 120, 80), index=pd.bdate_range(up.index[-1] + pd.Timedelta(days=1), periods=80))])
    st = market.regime_states(crash)
    assert st.iloc[-1] == "bear" and "pullback" in set(st)
    now = market.regime_now(st)
    assert now["state"] == "bear" and now["prev"] == "pullback" and now["days"] >= 1
    blip = up.copy()
    blip.iloc[-1] = float(up.rolling(60).mean().iloc[-1]) * 0.99  # one close under the 60-day average
    assert market.regime_states(blip).iloc[-1] == "bull"
    stats = market.forward_stats(market.regime_states(up), up)
    assert stats["bull"]["1w"]["up"] == 1.0 and stats["bull"]["share"] == 1.0


def test_align_uses_the_prior_close_for_overnight_markets():
    us = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    tw = pd.to_datetime(["2024-01-03", "2024-01-04", "2024-01-05"])
    assert list(market.align(us, tw, prior=True)) == [1.0, 2.0, 3.0]
    assert list(market.align(us, tw, prior=False)) == [2.0, 3.0, 3.0]


def test_linkage_recovers_beta_and_pending_move():
    rng = np.random.default_rng(3)
    days = pd.bdate_range("2023-01-02", periods=200)
    us_ret = rng.normal(0, 0.01, 200)
    us = pd.Series(100 * np.exp(np.cumsum(us_ret)), index=days)
    tw_ret = np.concatenate([[0.0], 0.5 * us_ret[:-1]]) + rng.normal(0, 0.001, 200)  # follows last night's US move
    tw = pd.Series(100 * np.exp(np.cumsum(tw_ret)), index=days)
    res = market.linkage(tw, [("US", "us", us, True)])
    d = res["drivers"][0]
    assert d["beta"] == pytest.approx(0.5, abs=0.05) and d["corr"] > 0.9 and res["r2"] > 0.8
    assert res["has_pending"] and res["implied_next"] == pytest.approx(0.5 * us_ret[-1], abs=0.002)


def test_risk_parts_total_and_levels():
    calm = {"regime": "bull", "drawdown": 0.0, "breadth": 80.0, "foreign20_ratio": 0.03, "foreign_streak": 4,
            "fut_pct": 0.9, "maint": 1.75, "vix": 13.0, "vix_term": 0.85, "oas": 2.8, "oas_chg10": 0.0,
            "soxx_r20": 0.1, "ndq_r20": 0.06, "fx_r20": -0.03, "fsi": 3.0, "liq_chg13": 0.04}
    parts = market.risk_parts(calm)
    assert len(parts) == 9 and market.risk_total(parts) < 10 and market.risk_level(market.risk_total(parts))["name"] == "低"
    panic = {"regime": "bear", "drawdown": -0.3, "breadth": 5.0, "foreign20_ratio": -0.08, "foreign_streak": -12,
             "fut_pct": 0.0, "maint": 1.35, "vix": 40.0, "vix_term": 1.2, "oas": 7.0, "oas_chg10": 1.5,
             "soxx_r20": -0.2, "ndq_r20": -0.1, "fx_r20": 0.04, "fsi": -3.0, "liq_chg13": -0.06}
    total = market.risk_total(market.risk_parts(panic))
    assert total > 90 and market.risk_level(total)["name"] == "高"
    some = market.risk_parts({"vix": 23.0, "maint": None})  # missing inputs are dropped, weights renormalise
    assert [p["key"] for p in some] == ["vol"] and market.risk_total(some) == pytest.approx(50)
    # the stress index overlaps the volatility and credit components, so it is not a component itself
    assert "stress" not in [p["key"] for p in parts] and "liquidity" in [p["key"] for p in parts]


def test_risk_history_and_bucket_stats():
    idx = pd.bdate_range("2023-01-02", periods=300)
    comps = {"trend": pd.Series(10.0, index=idx), "vol": pd.Series(20.0, index=idx), "credit": pd.Series(80.0, index=idx[100:])}
    hist = market.risk_history(comps, idx)
    assert len(hist) == 200 and hist.iloc[0] == pytest.approx((20 * 10 + 10 * 20 + 10 * 80) / 40, abs=0.1)
    close = pd.Series(np.linspace(100, 200, 300), index=idx)
    rows = market.bucket_stats(hist, close)
    assert rows[0]["level"] == "中等" and rows[0]["1w"]["up"] == 1.0


def test_evaluate_logs_only_count_matured_forecasts():
    f = make_df(np.linspace(100, 160, 60))
    d0, d_late = f.index[10].strftime("%Y-%m-%d"), f.index[58].strftime("%Y-%m-%d")
    log = {d0: {"A": [f["close"].iloc[10], 0.5, 0.5, -0.5], "B": [1, 0.5, None, None]}, d_late: {"A": [1, 0.5, 0.5, 0.5]}}
    res = market.evaluate_log(log, {"A": f}, scoring.HORIZONS)
    assert res["1w"] == {**res["1w"], "n": 1, "snapshots": 1, "hit": 1.0} and res["1m"]["hit"] == 1.0
    assert res["1q"] == {"n": 0, "snapshots": 0}  # 63 sessions have not passed
    ilog = {d0: {"A": {"close": 1, "h": {"1w": [0.6, 0.0, 1.0], "1m": [0.4, 0.0, 1e9]}}}}
    rows = market.evaluate_index_log(ilog, {"A": f}, scoring.HORIZONS)
    assert [(r["h"], r["dir_ok"], r["in_range"]) for r in rows] == [("1w", True, False), ("1m", False, True)]


def test_momentum_lists_and_alerts():
    base = {"code": "1", "market": "TW", "ma20": 100.0, "ma60": 95.0, "horizons": {"1w": {"p_up": 0.6}}, "factors": {"trend": 0.5, "momentum": 0.5}}
    strong = {**base, "symbol": "A", "name": "a", "close": 105.0, "tactical": 0.4, "r20": 0.05, "rsi": 55, "foreign_streak": 4, "structural": 0.3}
    fading = {**base, "symbol": "B", "name": "b", "close": 98.0, "tactical": 0.1, "r20": -0.02, "rsi": 50, "foreign_streak": -5,
              "factors": {"trend": 0.6, "momentum": 0.1}, "overall": 0.1}
    acc, weak = market.momentum_lists([strong, fading], {"A": 0.2, "B": 0.4})
    assert [r["symbol"] for r in acc] == ["A"] and "籌碼分數上升" in acc[0]["tags"]
    assert [r["symbol"] for r in weak] == ["B"] and {"籌碼分數下滑", "跌破月線", "外資連賣"} <= set(weak[0]["tags"])
    al = market.alerts({"risk": 75.0, "risk_prev": 60.0, "regime": "bear", "regime_prev_run": "pullback", "foreign_streak": -10,
                        "maint": 1.38, "vix_term": 1.1, "fut_pct": 0.0, "fut_net": -100000.0, "soxx_r20": -0.2})
    titles = [a["title"] for a in al]
    assert al[0]["level"] == 2 and "市場週期轉變" in titles and "融資維持率偏低" in titles and "VIX 期限結構倒掛" in titles
    assert market.alerts({"risk": 20.0, "regime": "bull", "foreign_streak": 6}) == [
        {"level": 0, "title": "外資連續買超", "text": "外資已連續 6 日買超。"}]


# ---------------- third batch: calibration, residual momentum, liquidity, pick changes ----------------
def _noise_frames(n_stocks=30, n=500, edge=0.0):
    """Synthetic technical frames: each stock has a fixed score, and `edge` sets how much daily drift it earns."""
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2023-01-02", periods=n)
    frames = {}
    for i in range(n_stocks):
        lvl = float(rng.uniform(-1, 1))
        score = pd.Series(lvl, index=idx)
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n) + edge * lvl * 0.004)), index=idx)
        frames[f"S{i}"] = pd.DataFrame({"close": close, "t_1w": score, "t_1m": score, "t_1q": score})
    return frames


def test_residual_momentum_ignores_what_the_market_explains():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2024-01-01", periods=320)
    mkt_r = rng.normal(0.001, 0.01, 320)
    bench = pd.Series(100 * np.exp(np.cumsum(mkt_r)), index=idx)
    noise = rng.normal(0, 0.004, 320)
    passenger = pd.Series(50 * np.exp(np.cumsum(1.5 * mkt_r + noise)), index=idx)            # only beta
    own = pd.Series(50 * np.exp(np.cumsum(1.5 * mkt_r + noise + 0.002)), index=idx)          # beta plus its own drift
    a, b = scoring.residual_momentum(passenger, bench), scoring.residual_momentum(own, bench)
    assert abs(a.iloc[-1]) < 1.0 and b.iloc[-1] > a.iloc[-1] + 1.5
    assert scoring.residual_momentum(passenger.head(100), bench).empty
    ic = scoring.factor_ic({"A": a}, {"A": passenger})
    assert set(ic) == {"1w", "1m", "1q"} and ic["1w"] is None                                # one stock is no cross-section


def test_net_liquidity_units_and_alignment():
    from pipeline import leading
    w = pd.date_range("2026-01-07", periods=3, freq="W-WED")
    walcl = pd.Series([6_700_000.0, 6_750_000.0, 6_760_000.0], index=w)
    tga = pd.Series([900_000.0, 950_000.0, 910_000.0], index=w)
    rrp = pd.Series([100.0, 50.0], index=[w[0] - pd.Timedelta(days=1), w[1] + pd.Timedelta(days=2)])  # daily, billions
    nl = leading.net_liquidity(walcl, tga, rrp)
    assert nl.iloc[0] == pytest.approx(6.7 - 0.9 - 0.1) and nl.iloc[1] == pytest.approx(6.75 - 0.95 - 0.1)
    assert nl.iloc[2] == pytest.approx(6.76 - 0.91 - 0.05)
    assert float(market.liquidity_risk(0.03)) == 0 and float(market.liquidity_risk(-0.05)) == 100


def test_pick_changes_compare_with_the_previous_week():
    from pipeline import leading
    mk = lambda d, syms: {"date": d, "picks": [{"symbol": x} for x in syms]}  # noqa: E731
    earlier = [mk("2026-10-28", ["A", "B", "C"]), mk("2026-11-04", ["B", "A", "D"]), mk("2026-11-10", ["Z", "A"])]
    ch = leading.pick_changes(["A", "E", "B"], earlier, "2026-11-11")   # 11/10 is the same week: not the reference
    assert ch["ref_date"] == "2026-11-04" and ch["dropped"] == ["D"]
    assert ch["by_symbol"]["A"] == {"new": False, "prev_rank": 2, "move": 1, "weeks": 3}
    assert ch["by_symbol"]["B"]["move"] == -2 and ch["by_symbol"]["E"] == {"new": True, "prev_rank": None, "move": None, "weeks": 1}
    only = leading.pick_changes(["A"], [mk("2026-11-10", ["Z", "A"])], "2026-11-11")       # nothing older: use what there is
    assert only["ref_date"] == "2026-11-10" and only["by_symbol"]["A"]["prev_rank"] == 2
    assert only["by_symbol"]["A"]["weeks"] == 1                                             # same week: still the first
    assert leading.pick_changes(["A"], [], "2026-11-11")["ref_date"] is None


# ---------------- 2026-11 review: one slow score, relative strength, price integrity, flags ----------------
def test_forecast_is_a_range_around_todays_price():
    for sc in (-1, 0, 1):
        f = scoring.forecast(100, 0.02, sc, 21, p_up=0.57)
        assert f["exp_ret"] == 0 and f["low"] < 100 < f["high"] and f["p_up"] == 0.57
    assert scoring.forecast(100, 0.02, 1, 21)["p_up"] is None
    assert scoring.forecast(100, float("nan"), 0.2, 5)["sigma"] > 0  # NaN vol falls back


def test_one_slow_score_and_the_auxiliary_reads():
    tf = scoring.tech_frame(make_df(trend(400, 0.003)))
    assert tf["t_1w"].equals(tf["t_slow"]) and tf["t_1q"].equals(tf["t_slow"])
    assert tf["hi52"].dropna().le(1.0 + 1e-9).all()
    tw = scoring.score_stock(tf, 0.8, -0.5, 0.0, tech=0.4)
    us = scoring.score_stock(tf, 0.8, -0.5, 0.0, slow_chips=True, tech=0.4)
    assert {f["score"] for f in tw["horizons"].values()} == {round(0.60 * 0.4 + 0.25 * 0.8 + 0.15 * -0.5, 3)}
    assert us["horizons"]["1w"]["score"] == pytest.approx(0.80 * 0.4 + 0.05 * 0.8 + 0.15 * -0.5, abs=2e-3)
    assert tw["tactical"] == 0.8 and tw["structural"] == pytest.approx(-0.5)   # chips, fundamentals
    assert us["tactical"] is None and us["structural"] == pytest.approx(0.7 * -0.5 + 0.3 * 0.8)
    assert scoring.score_stock(tf, 0.8, -0.5, 0.3, tech=0.4)["overall"] == tw["overall"]  # buzz weighs nothing
    assert scoring.setup_tag(0.3, 0.4) == "基本面與籌碼同步偏多"
    assert scoring.setup_tag(0.3, 0.4, tactical_prev=0.0) == "基本面佳，籌碼剛轉強"
    assert scoring.setup_tag(-0.3, 0.4) == "基本面佳但籌碼偏弱"


def test_tech_panel_ranks_within_the_market():
    idx = pd.bdate_range("2025-01-01", periods=3)
    mk = lambda slow, hi: pd.DataFrame({"t_slow": slow, "hi52": hi}, index=idx)  # noqa: E731
    frames = {"A": mk([0.9] * 3, [0.5] * 3), "B": mk([0.1] * 3, [1.0] * 3), "C": mk([-0.5] * 3, [0.7] * 3)}
    us = scoring.tech_panel(frames, "US").iloc[-1]
    assert us["A"] == pytest.approx(1.0) and us["C"] == pytest.approx(2 / 3 * 0.5 * 2 - 1)
    tw = scoring.tech_panel(frames, "TW").iloc[-1]
    assert tw["B"] > us["B"] and tw["A"] < us["A"]                              # the 52-week high moves B up


def _rel_world(edge, n_stocks=40, n=500):
    rng = np.random.default_rng(11)
    idx = pd.bdate_range("2023-01-02", periods=n)
    lv = pd.Series(np.linspace(-1, 1, n_stocks), index=[f"S{i}" for i in range(n_stocks)])
    panel = pd.DataFrame({s: [v] * n for s, v in lv.items()}, index=idx)
    market = np.cumsum(rng.normal(0.001, 0.01, n))                             # a rising market for everyone
    closes = pd.DataFrame({s: 100 * np.exp(market + np.cumsum(rng.normal(0, 0.01, n) + edge * v * 0.002))
                           for s, v in lv.items()}, index=idx)
    return panel, closes


def test_fit_relative_finds_skill_only_where_it_exists():
    none = market.fit_relative(*_rel_world(0.0), {"1m": 21})["1m"]
    assert none["k"] < 0.05 and not none["edge"] and none["base_up"] > 0.5      # the market rose; no skill
    real = market.fit_relative(*_rel_world(1.0), {"1m": 21})["1m"]
    assert real["k"] > 0.1 and real["edge"] and real["brier"] < real["brier_coin"]
    assert sum(b["n"] for b in real["bins"]) == real["n"]
    assert market.p_beat(1.0, 0.1) == pytest.approx(0.6) and market.p_beat(-1.0, 0.5) == 0.3


def test_sanitize_removes_bad_bars_and_adjusts_actions():
    from pipeline import prices
    idx = pd.bdate_range("2026-08-03", periods=40)
    c = pd.Series(100.0, index=idx)
    c.iloc[20:] = 34.0                                       # 1-for-3 split on idx[20]
    c.iloc[10] = 300.0                                       # one bad bar, undone the next day
    df = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0})
    sunday = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
                          index=[pd.Timestamp("2026-08-09")])
    df = pd.concat([df, sunday]).sort_index()
    ev = {idx[20].strftime("%Y-%m-%d"): 0.34}
    out, applied, notes, total = prices.sanitize(df, market="TW", code="6669", calendar=idx, events=ev)
    r = out["close"].pct_change().dropna()
    assert r.abs().max() < 0.105 and len(out) == 39 and applied and total == pytest.approx(0.34)
    again, applied2, _, total2 = prices.sanitize(out, market="TW", code="6669", calendar=idx, events=ev, applied=applied)
    assert total2 == 1.0 and applied2 == applied                              # never applied twice
    gap = c.copy(); gap.iloc[25:] = 50.0                                       # an unannounced seam
    seam = pd.DataFrame({"open": gap, "high": gap, "low": gap, "close": gap, "volume": 1.0})
    fixed, *_ = prices.sanitize(seam.drop(idx[10]), market="TW", code="2330", calendar=idx)
    assert fixed["close"].pct_change().abs().max() < 0.105
    us = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
                      index=pd.DatetimeIndex(["2025-05-23", "2025-05-26", "2025-05-27"]))
    assert list(prices.sanitize(us, market="US")[0].index.day) == [23, 27]    # Memorial Day
    assert pd.Timestamp("2026-04-03").date() in prices.nyse_holidays(2026)      # Good Friday


def test_trading_flags_and_insider_parsing():
    fl = twse.parse_trading_flags(
        [{"Code": "3189", "DispositionPeriod": "115/11/10～115/11/16", "DispositionMeasures": "第一次處置", "ReasonsOfDisposition": "連續三次"}],
        [{"Code": "", "NumberOfAnnouncement": "0"}, {"Code": "2455", "NumberOfAnnouncement": "2", "TradingInfoForAttention": "x", "Date": "1151113"}],
        [{"Code": "6108", "RecentlyMetAttentionSecuritiesCriteria": "連續二次"}],
        [{"Code": "00878", "Date": "1151117", "Exdividend": "息", "CashDividend": "1.250000"}])
    assert fl["3189"]["punish"] == {"from": "2026-11-10", "to": "2026-11-16", "measure": "第一次處置", "reason": "連續三次"}
    assert fl["2455"]["notice"]["count"] == 2 and "" not in fl and fl["6108"]["near_punish"] == "連續二次"
    assert fl["00878"]["exdiv"] == {"date": "2026-11-17", "kind": "息", "cash": 1.25}


def test_previous_week_picks_ignore_the_same_week():
    from pipeline import leading
    c = [{"date": "2026-11-04", "picks": [{"symbol": "A"}]}, {"date": "2026-11-11", "picks": [{"symbol": "B"}]},
         {"date": "2026-11-13", "picks": [{"symbol": "C"}]}]
    assert leading.previous_week_picks(c, "2026-11-13") == {"A"}
    assert leading.previous_week_picks(c, "2026-11-18") == {"C"}
    assert leading.previous_week_picks([], "2026-11-18") == set()


# ---------------- OTC, foreign holding, TAIFEX history, Form 4 ----------------
def test_tpex_tables():
    from pipeline import tpex
    q = tpex.parse_day_quotes({"fields": ["代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "成交股數  "],
                               "data": [["6488", "環球晶", "1,085.00", "+5", "1,055.00", "1,135.00", "1,045.00", "24,330,000"],
                                        ["1234", "沒成交", "---", "", "", "", "", "0"]]})
    assert q == {"6488": (1055.0, 1135.0, 1045.0, 1085.0, 24330000.0)}
    row = ["6488", "環球晶", "1", "1", "741,269", "0", "0", "0", "1", "1", "741,269", "1", "1", "-341,881",
           "1", "1", "-77,499", "1", "1", "-116,695", "1", "1", "-194,194", "205,194"]
    assert tpex.parse_inst({"data": [row]}) == {"6488": (741269.0, -341881.0, -194194.0)}
    bad = row[:]; bad[23] = "999"
    with pytest.raises(ValueError):
        tpex.parse_inst({"data": [bad]})
    fl = tpex.parse_flags([{"SecuritiesCompanyCode": "6147", "DispositionPeriod": "1151112~1151118", "DispositionReasons": "連續3個營業日"}],
                          [{"SecuritiesCompanyCode": "1815", "TradingInformation": "當沖比率", "Date": "1151113"}],
                          [{"SecuritiesCompanyCode": "3105", "ExRrightsExDividendDate": "1151120", "ExRrightsExDividend": "除息", "CashDividend": "1.5"}])
    assert fl["6147"]["punish"]["to"] == "2026-11-18" and fl["1815"]["notice"]["count"] == 1
    assert fl["3105"]["exdiv"] == {"date": "2026-11-20", "kind": "息", "cash": 1.5}


def test_foreign_holding_parser():
    j = {"stat": "OK", "fields": ["證券代號", "證券名稱", "國際證券編碼", "發行股數", "外資及陸資尚可投資股數", "全體外資及陸資持有股數",
                                  "外資及陸資尚可投資比率", "全體外資及陸資持股比率", "外資及陸資共用法令投資上限比率"],
         "data": [["2330", "台積電", "x", "1", "1", "1", 31.89, 68.1, "100.00"]]}
    orig = twse._rwd
    twse._rwd = lambda *a, **k: j
    try:
        assert twse.foreign_holding("2026-11-12")["2330"] == {"pct": 68.1, "room": 31.89, "limit": 100.0}
    finally:
        twse._rwd = orig


def test_taifex_history_parsers():
    from pipeline import leading
    fut = [["2026/10/14", "TX", "202610", "1", "1", "1", "1", "1", "1", "1", "48798", "105601", "", "", "", "", "", "一般"],
           ["2026/10/14", "TX", "202611", "1", "1", "1", "1", "1", "1", "1", "48900", "20000", "", "", "", "", "", "一般"],
           ["2026/10/14", "TX", "202610", "1", "1", "1", "1", "1", "1", "1", "-", "-", "", "", "", "", "", "盤後"]]
    assert leading.parse_front_settle(fut) == {"2026-10-14": 48798.0}
    assert leading.parse_total_oi(fut) == {"2026-10-14": 125601.0}
    assert leading.parse_pcr_hist([["2026/11/12", "1", "1", "98", "1", "1", "66.69"]]) == {"2026-11-12": pytest.approx(0.6669)}
    lt = [["2026/10/14", "TX", "臺股期貨", "999999", "0", "1", "1", "85177", "83794", "126583"],
          ["2026/10/14", "TX", "臺股期貨", "999999", "1", "1", "1", "1", "999", "126583"],
          ["2026/10/14", "TX", "臺股期貨", "202610", "0", "1", "1", "1", "999", "126583"]]
    assert leading.parse_top10_hist(lt) == {"2026-10-14": pytest.approx((85177 - 83794) / 126583)}
    cp = [["2026/10/14", "臺指選擇權", "CALL", "外資及陸資", *["0"] * 10, "2250"],
          ["2026/10/14", "臺指選擇權", "PUT", "外資及陸資", *["0"] * 10, "-4306"],
          ["2026/10/14", "臺指選擇權", "CALL", "投信", *["0"] * 10, "99"]]
    assert leading.parse_foreign_options(cp) == {"2026-10-14": {"call": 2250.0, "put": -4306.0}}
    assert leading.options_stance(2250, -4306) == 1 and leading.options_stance(-1, 5) == -1 and leading.options_stance(1, 1) == 0
    inst = [["2026/10/14", "小型臺指期貨", "自營商", *["0"] * 10, "-500"], ["2026/10/14", "小型臺指期貨", "外資", *["0"] * 10, "-1500"],
            ["2026/10/14", "臺股期貨", "外資", *["0"] * 10, "-9999"]]
    assert leading.parse_inst_net_oi(inst, "小型臺指期貨") == {"2026-10-14": -2000.0}


FORM4 = """<?xml version="1.0"?><ownershipDocument>
<reportingOwner><reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner>
<aff10b5One>0</aff10b5One>
<nonDerivativeTable>
<nonDerivativeTransaction><transactionDate><value>2026-11-04</value></transactionDate><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>50.5</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction><transactionDate><value>2026-11-04</value></transactionDate><transactionCoding><transactionCode>G</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>9</value></transactionShares><transactionPricePerShare><value>0</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""


def test_form4_keeps_open_market_trades_only():
    t = us_data.parse_form4(FORM4)
    assert t == [{"date": "2026-11-04", "owner": "Doe Jane", "role": "CFO", "type": "buy", "shares": 1000.0,
                  "value": 50500.0, "planned": False}]
    s = us_data.insider_summary(t + [{"date": "2026-01-01", "type": "sell", "value": 9e9, "owner": "x", "planned": False}], "2026-08-01")
    assert s["buys"] == 1 and s["sells"] == 0 and s["buyers"] == 1 and s["last"] == "2026-11-04"


def test_carry_flags_keeps_only_unexpired_facts():
    f = {"3189": {"punish": {"from": "2026-11-03", "to": "2026-11-16"}, "notice": {"count": 1}},
         "2455": {"punish": {"from": "2026-10-01", "to": "2026-11-10"}},
         "0056": {"exdiv": {"date": "2026-11-20", "kind": "息", "cash": 1.0}}}
    out = twse.carry_flags(f, "2026-11-14")
    assert out == {"3189": {"punish": {"from": "2026-11-03", "to": "2026-11-16"}},
                   "0056": {"exdiv": {"date": "2026-11-20", "kind": "息", "cash": 1.0}}}
