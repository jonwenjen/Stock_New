"""Taiwan Stock Exchange / TAIFEX open data: flows, margin, valuation, revenue, insiders."""
from __future__ import annotations

import re
import time
from collections import defaultdict

from . import http

OPENAPI = "https://openapi.twse.com.tw/v1"
RWD = "https://www.twse.com.tw/rwd/zh"
COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")  # excludes ETFs (00xx), warrants, etc.


def num(s) -> float | None:
    """'1,234.5' -> 1234.5 ; '' / '--' -> None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "--", "N/A", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc_to_iso(s: str) -> str | None:
    """'1151109' or '115/11/09' -> '2026-11-09'."""
    digits = re.sub(r"\D", "", str(s or ""))
    if len(digits) != 7:
        return None
    return f"{int(digits[:3]) + 1911}-{digits[3:5]}-{digits[5:7]}"


class NoData(Exception):
    """TWSE answered, but has nothing for that date (holiday / not published yet)."""


def _rwd(path: str, **params) -> dict:
    params["response"] = "json"
    # No in-place retry: when TWSE throttles, the refusal sticks for a while, so
    # fetch_days() cools down and re-queues the date instead of hammering it.
    j = http.get_json(f"{RWD}/{path}", params=params, retries=0)
    if j.get("stat") != "OK":
        raise NoData(f"{path} {params.get('date') or params.get('dayDate')}: {j.get('stat')}")
    return j


def fetch_days(dates: list[str], fn, label: str = "", log=print, cooldown: float = 75.0) -> dict:
    """Call fn(date) for each date, backing off whenever TWSE throttles.

    A refusal triggers a cool-down and re-queues the date for one later pass.
    Three refusals in a row end the pass: whatever is still missing is simply
    picked up by the next run, because every caller persists what it got.
    """
    out, retry = {}, []
    for pass_no, todo in enumerate((list(dates), retry)):
        if pass_no and todo:
            log(f"  {label}: retrying {len(todo)} throttled requests after a pause")
            time.sleep(cooldown)
        streak_fail = 0
        for i, d in enumerate(todo):
            try:
                out[d] = fn(d)
                streak_fail = 0
            except NoData as e:
                streak_fail = 0
                log(f"  {label} {e}")
            except Exception as e:  # noqa: BLE001 - throttled or network error
                streak_fail += 1
                if pass_no == 0:
                    retry.append(d)
                else:
                    log(f"  {label} {d}: {e}")
                if streak_fail >= 3:
                    left = todo[i + 1:]
                    log(f"  {label}: still throttled, leaving {len(left)} requests for later")
                    if pass_no == 0:
                        retry.extend(left)
                    break
                time.sleep(cooldown)
            if i and i % 40 == 0:
                log(f"  {label} {i}/{len(todo)}")
    return out


# ----------------------------- snapshots (OpenAPI) ----------------------------- #
def quotes() -> dict[str, dict]:
    out = {}
    for r in http.get_json(f"{OPENAPI}/exchangeReport/STOCK_DAY_ALL"):
        code = r["Code"].strip()
        out[code] = {"name": r["Name"].strip(), "close": num(r["ClosingPrice"]),
                     "change": num(r["Change"]), "volume": num(r["TradeVolume"]),
                     "value": num(r["TradeValue"]), "date": roc_to_iso(r.get("Date"))}
    return out


def valuation() -> dict[str, dict]:
    return {r["Code"].strip(): {"pe": num(r["PEratio"]), "yield": num(r["DividendYield"]),
                                "pb": num(r["PBratio"])}
            for r in http.get_json(f"{OPENAPI}/exchangeReport/BWIBBU_ALL")}


def monthly_revenue() -> dict[str, dict]:
    out = {}
    for r in http.get_json(f"{OPENAPI}/opendata/t187ap05_L"):
        ym = str(r.get("資料年月", ""))
        out[r["公司代號"].strip()] = {
            "industry": (r.get("產業別") or "").strip(),
            "month": f"{int(ym[:-2]) + 1911}-{ym[-2:]}" if len(ym) >= 4 else None,
            "revenue": num(r.get("營業收入-當月營收")),  # thousand TWD
            "rev_mom": num(r.get("營業收入-上月比較增減(%)")),
            "rev_yoy": num(r.get("營業收入-去年同月增減(%)")),
            "rev_cum_yoy": num(r.get("累計營業收入-前期比較增減(%)")),
        }
    return out


def quarterly_eps() -> dict[str, dict]:
    out = {}
    for r in http.get_json(f"{OPENAPI}/opendata/t187ap14_L"):
        out[r["公司代號"].strip()] = {
            "eps": num(r.get("基本每股盈餘(元)")),
            "eps_period": f"{int(r['年度']) + 1911}Q{r['季別']}" if r.get("年度") else None,
            "net_income": num(r.get("稅後淨利")),
        }
    return out


def insiders() -> dict[str, dict]:
    """Directors/supervisors holdings per company (monthly filing)."""
    agg: dict[str, dict] = defaultdict(lambda: {"held": 0.0, "elected": 0.0, "pledged": 0.0,
                                                "people": []})
    month = None
    for r in http.get_json(f"{OPENAPI}/opendata/t187ap11_L", timeout=120):
        code = r["公司代號"].strip()
        held, elected = num(r.get("目前持股")) or 0, num(r.get("選任時持股 ")) or 0
        pledged = num(r.get("設質股數")) or 0
        a = agg[code]
        a["name"] = r.get("公司名稱", "").strip()
        a["held"] += held
        a["elected"] += elected
        a["pledged"] += pledged
        a["people"].append((held, r.get("職稱", "").strip(), r.get("姓名", "").strip(), pledged))
        ym = str(r.get("資料年月", ""))
        if len(ym) >= 4:
            month = f"{int(ym[:-2]) + 1911}-{ym[-2:]}"
    out = {}
    for code, a in agg.items():
        top = sorted(a["people"], reverse=True)[:3]
        out[code] = {
            "name": a["name"], "held": a["held"], "elected": a["elected"],
            "chg_since_elected": (a["held"] / a["elected"] - 1) if a["elected"] else None,
            "pledge_ratio": (a["pledged"] / a["held"]) if a["held"] else 0.0,
            "top": [{"title": t, "name": n, "held": h} for h, t, n, _ in top],
            "month": month,
        }
    return out


def margin_latest() -> dict[str, dict]:
    return {r["股票代號"].strip(): {"margin": num(r["融資今日餘額"]), "margin_prev": num(r["融資前日餘額"]),
                                    "short": num(r["融券今日餘額"]), "name": r["股票名稱"].strip()}
            for r in http.get_json(f"{OPENAPI}/exchangeReport/MI_MARGN")}


def foreign_futures() -> dict | None:
    """Foreign investors' net open interest in TAIEX futures (contracts)."""
    url = ("https://openapi.taifex.com.tw/v1/"
           "MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate")
    for r in http.get_json(url):
        if r.get("ContractCode") == "臺股期貨" and r.get("Item", "").startswith("外資"):
            d = r["Date"]
            return {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "net_oi": num(r["OpenInterest(Net)"]),
                    "net_trade": num(r["TradingVolume(Net)"])}
    return None


# ----------------------------- dated queries (rate-limited) ----------------------------- #
def inst_market(date: str) -> dict:
    """Three-institution net buy/sell for the whole market on YYYY-MM-DD (100M TWD)."""
    j = _rwd("fund/BFI82U", type="day", dayDate=date.replace("-", ""))
    row = {r[0]: num(r[3]) or 0.0 for r in j["data"]}
    yi = 1e8
    dealer = sum(v for k, v in row.items() if k.startswith("自營商"))
    foreign = sum(v for k, v in row.items() if k.startswith("外資"))
    return {"foreign": round(foreign / yi, 2), "trust": round(row.get("投信", 0) / yi, 2),
            "dealer": round(dealer / yi, 2), "total": round(row.get("合計", 0) / yi, 2)}


def margin_market(date: str) -> dict:
    """Market margin summary: balance in 100M TWD, short balance in lots."""
    j = _rwd("marginTrading/MI_MARGN", date=date.replace("-", ""), selectType="MS")
    rows = {r[0]: r for r in j["tables"][0]["data"]}
    money = rows["融資金額(仟元)"]
    return {"margin_bal": round((num(money[5]) or 0) / 1e5, 2),
            "margin_units": num(rows["融資(交易單位)"][5]),
            "short_units": num(rows["融券(交易單位)"][5])}


def margin_stocks(date: str) -> dict[str, dict]:
    """Per-stock margin/short balances (lots) on a given date."""
    j = _rwd("marginTrading/MI_MARGN", date=date.replace("-", ""), selectType="ALL")
    table = next(t for t in j["tables"] if t.get("data") and len(t["data"][0]) >= 13)
    return {r[0].strip(): {"margin": num(r[6]), "short": num(r[12])} for r in table["data"]}


def inst_stocks(date: str) -> dict[str, tuple[float, float, float]]:
    """Per-stock net shares (foreign, trust, dealer) on a given date."""
    j = _rwd("fund/T86", date=date.replace("-", ""), selectType="ALLBUT0999")
    f = j["fields"]
    i_f, i_fd = f.index("外陸資買賣超股數(不含外資自營商)"), f.index("外資自營商買賣超股數")
    i_t, i_d = f.index("投信買賣超股數"), f.index("自營商買賣超股數")
    return {r[0].strip(): ((num(r[i_f]) or 0) + (num(r[i_fd]) or 0), num(r[i_t]) or 0, num(r[i_d]) or 0)
            for r in j["data"]}


def day_quotes(date: str) -> dict[str, tuple]:
    """Official daily OHLCV for every listed common stock: {code: (o, h, l, c, shares)}."""
    j = _rwd("afterTrading/MI_INDEX", date=date.replace("-", ""), type="ALLBUT0999")
    table = next(t for t in j["tables"] if "證券代號" in (t.get("fields") or []))
    f = table["fields"]
    ix = [f.index(k) for k in ("開盤價", "最高價", "最低價", "收盤價", "成交股數")]
    out = {}
    for r in table["data"]:
        code = r[0].strip()
        if not COMMON_STOCK.match(code):
            continue
        o, h, l, c, v = (num(r[i]) for i in ix)
        if c:  # no trade that day -> skip
            out[code] = (o or c, h or c, l or c, c, v or 0)
    return out


def taiex_month(month: str) -> list[tuple]:
    """TAIEX daily OHLC for a month ('YYYY-MM') -> [(iso_date, o, h, l, c), ...]."""
    j = _rwd("TAIEX/MI_5MINS_HIST", date=month.replace("-", "") + "01")
    return [(roc_to_iso(r[0]), *(num(x) for x in r[1:5])) for r in j["data"]]


def stock_month(code: str, month: str) -> list[tuple]:
    """One stock, one month ('YYYY-MM') of daily bars -> [(iso_date, o, h, l, c, shares), ...]."""
    j = _rwd("afterTrading/STOCK_DAY", date=month.replace("-", "") + "01", stockNo=code)
    out = []
    for r in j["data"]:
        o, h, l, c = (num(x) for x in r[3:7])
        if c:
            out.append((roc_to_iso(r[0]), o or c, h or c, l or c, c, num(r[1]) or 0))
    return out


def streak(values: list[float]) -> int:
    """Signed count of the latest consecutive same-direction days (newest last)."""
    n = 0
    for v in reversed(values):
        if v == 0 or (n and (v > 0) != (n > 0)):
            break
        n += 1 if v > 0 else -1
    return n
