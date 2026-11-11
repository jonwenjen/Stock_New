"""Unit tests for the pure parts of the pipeline (no network)."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from pipeline import indicators as ind
from pipeline import media, scoring, twse, us_data


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
    assert mixed == pytest.approx((0.45 - 0.25) / 0.70)


def test_forecast_is_bounded_and_ordered():
    for s in (-1, -0.3, 0, 0.3, 1):
        f = scoring.forecast(100, 0.02, s, 21)
        assert 0.28 <= f["p_up"] <= 0.72
        assert f["low"] < 100 * (1 + f["exp_ret"]) < f["high"]
    assert scoring.forecast(100, 0.02, 1, 21)["exp_ret"] > 0 > scoring.forecast(100, 0.02, -1, 21)["exp_ret"]
    assert scoring.forecast(100, float("nan"), 0.2, 5)["sigma"] > 0  # NaN vol falls back


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
