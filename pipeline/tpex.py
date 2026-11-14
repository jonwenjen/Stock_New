"""Taipei Exchange (TPEx, 櫃買中心): over-the-counter stocks.

Two kinds of source, like twse.py:
  * dated tables on www.tpex.org.tw - every OTC security's daily quote and every security's
    institutional trades for one date per request (used to build and extend the price store);
  * the TPEx OpenAPI - latest snapshots (valuation, revenue, EPS, margin, foreign holding,
    trading flags, ex-rights).
Codes do not overlap with listed (TWSE) codes, so the results merge into the same dicts.
"""
from __future__ import annotations

import re

from . import http
from .twse import NoData, _roc_date, num

WWW = "https://www.tpex.org.tw/www/zh-tw"
OPENAPI = "https://www.tpex.org.tw/openapi/v1"
http.HOST_INTERVAL.setdefault("www.tpex.org.tw", 3.0)
COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")


def _table(path: str, date: str, **params) -> dict:
    j = http.get_json(f"{WWW}/{path}", params={"date": date.replace("-", "/"), "response": "json", **params}, retries=0)
    tables = j.get("tables") or []
    if not tables or not tables[0].get("data"):
        raise NoData(f"TPEx {path} {date}: no data")
    return tables[0]


def parse_day_quotes(table: dict) -> dict[str, tuple]:
    """Daily quote table -> {code: (open, high, low, close, shares)}; untraded securities are left out."""
    f = [x.strip() for x in table["fields"]]
    ix = [f.index(k) for k in ("開盤", "最高", "最低", "收盤", "成交股數")]
    out = {}
    for r in table["data"]:
        o, h, l, c, v = (num(r[i]) for i in ix)  # noqa: E741
        if c:
            out[r[0].strip()] = (o or c, h or c, l or c, c, v or 0)
    return out


def day_quotes(date: str) -> dict[str, tuple]:
    return parse_day_quotes(_table("afterTrading/otc", date, type="EW"))


def parse_inst(table: dict) -> dict[str, tuple[float, float, float]]:
    """Institutional trades table -> {code: (foreign net, trust net, dealer net)} in shares.

    The header repeats 買進/賣出/買賣超 for each group, so columns are taken by position and
    checked: foreign (excl. dealers) + foreign dealers must equal the foreign total, and the
    three groups must add up to the grand total."""
    out = {}
    for r in table["data"]:
        if len(r) < 24:
            continue
        v = [num(x) or 0.0 for x in r]
        if abs(v[4] + v[7] - v[10]) > 1 or abs(v[10] + v[13] + v[22] - v[23]) > 1:
            raise ValueError("TPEx institutional table layout changed")
        out[r[0].strip()] = (v[10], v[13], v[22])
    return out


def inst_stocks(date: str) -> dict[str, tuple[float, float, float]]:
    return parse_inst(_table("insti/dailyTrade", date, type="Daily", sect="EW"))


def _api(path: str) -> list[dict]:
    return http.get_json(f"{OPENAPI}/{path}", timeout=90)


def valuation() -> dict[str, dict]:
    return {r["SecuritiesCompanyCode"].strip(): {"pe": num(r.get("PriceEarningRatio")), "pb": num(r.get("PriceBookRatio")),
                                                 "yield": num(r.get("YieldRatio"))} for r in _api("tpex_mainboard_peratio_analysis")}


def monthly_revenue() -> dict[str, dict]:
    out = {}
    for r in _api("mopsfin_t187ap05_O"):
        ym = str(r.get("資料年月", ""))
        out[r["公司代號"].strip()] = {
            "industry": (r.get("產業別") or "").strip() or "其他",
            "month": f"{int(ym[:-2]) + 1911}-{ym[-2:]}" if len(ym) >= 4 else None,
            "revenue": num(r.get("營業收入-當月營收")),
            "rev_mom": num(r.get("營業收入-上月比較增減(%)")), "rev_yoy": num(r.get("營業收入-去年同月增減(%)")),
            "rev_cum_yoy": num(r.get("累計營業收入-前期比較增減(%)"))}
    return out


def quarterly_eps() -> dict[str, dict]:
    out = {}
    for r in _api("mopsfin_t187ap14_O"):
        e = num(r.get("基本每股盈餘"))
        if e is not None:
            out[r["SecuritiesCompanyCode"].strip()] = {"eps": e, "eps_period": f"{int(r['Year']) + 1911}Q{r['季別']}"}
    return out


def margin_latest() -> dict[str, dict]:
    return {r["SecuritiesCompanyCode"].strip(): {"name": r.get("CompanyName", "").strip(),
                                                 "margin": num(r.get("MarginPurchaseBalance")),
                                                 "limit": num(r.get("MarginPurchaseQuota")),
                                                 "short": num(r.get("ShortSaleBalance"))}
            for r in _api("tpex_mainboard_margin_balance")}


def foreign_holding() -> dict[str, dict]:
    out = {}
    for r in _api("tpex_3insti_qfii"):
        p, room = num(r.get("PercentageOfSharesOC/FMIHeld")), num(r.get("PercentageOfAvailableInvestmentForOC/FI"))
        if p is not None:
            out[r["SecuritiesCompanyCode"].strip()] = {"pct": p, "room": room}
    return out


_CN = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _times(text: str) -> int | None:
    """'…連續四次' -> 4."""
    m = re.search(r"連續([一二兩三四五六七八九十\d]+)次", text or "")
    if not m:
        return None
    t = m.group(1)
    return int(t) if t.isdigit() else _CN.get(t)


def parse_flags(disposal: list[dict], warning: list[dict], exdiv: list[dict],
                note: list[dict] | None = None) -> dict[str, dict]:
    """Same shape as twse.parse_trading_flags: punish / notice / near_punish / exdiv. `note` is the
    warning-accumulation list (e.g. "連續四次"), the step before a disposition."""
    out: dict[str, dict] = {}
    for r in disposal:
        code = (r.get("SecuritiesCompanyCode") or "").strip()
        period = (r.get("DispositionPeriod") or "").split("~")
        if code and len(period) == 2:
            out.setdefault(code, {})["punish"] = {"from": _roc_date(period[0]), "to": _roc_date(period[1]),
                                                  "measure": "處置", "reason": (r.get("DispositionReasons") or "").strip()}
    for r in warning:
        code = (r.get("SecuritiesCompanyCode") or "").strip()
        if code:
            out.setdefault(code, {})["notice"] = {"count": 1, "info": (r.get("TradingInformation") or "").strip()[:80],
                                                  "date": _roc_date(r.get("Date"))}
    for r in note or []:
        code, text = (r.get("SecuritiesCompanyCode") or "").strip(), (r.get("AccumulationSituation") or "").strip()
        if code and text:
            out.setdefault(code, {})["near_punish"] = text
            n = _times(text)
            if n and "notice" in out[code]:
                out[code]["notice"]["count"] = n
    for r in exdiv:
        code, d = (r.get("SecuritiesCompanyCode") or "").strip(), _roc_date(r.get("ExRrightsExDividendDate"))
        if code and d:
            out.setdefault(code, {})["exdiv"] = {"date": d, "kind": (r.get("ExRrightsExDividend") or "").replace("除", "").strip() or "息",
                                                 "cash": num(r.get("CashDividend"))}
    return out


def trading_flags() -> dict[str, dict]:
    try:
        note = _api("tpex_trading_warning_note")
    except Exception:  # noqa: BLE001 - the precursor list is extra; the core flags stand without it
        note = []
    return parse_flags(_api("tpex_disposal_information"), _api("tpex_trading_warning_information"),
                       _api("tpex_exright_prepost"), note)


def ex_rights_recent() -> dict[str, dict[str, float]]:
    """Ex-rights / ex-dividend events of the last days: {code: {date: reference / previous close}}."""
    out: dict[str, dict[str, float]] = {}
    for r in _api("tpex_exright_daily"):
        prev, ref, d = num(r.get("ClosePriceBeforeExRightsDiviend")), num(r.get("ExRightsDiviendQuote")), _roc_date(r.get("Date"))
        if prev and ref and d:
            out.setdefault(r["SecuritiesCompanyCode"].strip(), {})[d] = round(ref / prev, 6)
    return out
