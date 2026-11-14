"""US data: Congress trades, 13F holdings of well-known investors, SEC fundamentals."""
from __future__ import annotations

import ast
import datetime as dt
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

from . import http

SEC = "https://data.sec.gov"


# ----------------------------- Congress trades ----------------------------- #
def parse_quiver(html: str) -> list[dict]:
    """Extract the `recentTradesData` JS array embedded in Quiver's congress page."""
    m = re.search(r"let\s+recentTradesData\s*=\s*(\[\[.*?\]\])\s*;", html, re.S)
    if not m:
        raise RuntimeError("recentTradesData not found")
    rows = ast.literal_eval(m.group(1).replace("null", "None").replace("NaN", "None"))
    out = []
    for r in rows:
        if len(r) < 10 or not r[0] or r[0] == "-":
            continue
        kind = str(r[3])
        out.append({
            "ticker": str(r[0]).upper().replace(".", "-"), "asset": str(r[1])[:80],
            "type": "buy" if "Purchase" in kind else "sell" if "Sale" in kind else "other",
            "amount": str(r[4]), "name": str(r[5]), "chamber": str(r[6]), "party": str(r[7]),
            "filed": str(r[8])[:10], "traded": str(r[9])[:10],
        })
    return out


def congress_trades() -> list[dict]:
    html = http.get_text("https://www.quiverquant.com/congresstrading/", timeout=60)
    return parse_quiver(html)


def congress_summary(trades: list[dict], today: dt.date, days: int = 90) -> dict[str, dict]:
    since = (today - dt.timedelta(days=days)).isoformat()
    agg: dict[str, dict] = defaultdict(lambda: {"buys": 0, "sells": 0, "members": set()})
    for t in trades:
        if t["traded"] < since or t["type"] == "other":
            continue
        a = agg[t["ticker"]]
        a["buys" if t["type"] == "buy" else "sells"] += 1
        a["members"].add(t["name"])
    return {k: {"buys": v["buys"], "sells": v["sells"], "net": v["buys"] - v["sells"],
                "members": sorted(v["members"])} for k, v in agg.items()}


# ----------------------------- 13F ----------------------------- #
def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_13f(xml_text: str) -> dict[str, dict]:
    """Information table -> {cusip: {name, value, shares}} (options excluded)."""
    root = ET.fromstring(xml_text)
    out: dict[str, dict] = {}
    for it in root.iter():
        if _strip_ns(it.tag) != "infoTable":
            continue
        rec = {_strip_ns(e.tag): (e.text or "").strip() for e in it.iter()}
        if rec.get("putCall"):
            continue
        cusip = rec.get("cusip", "").upper()
        a = out.setdefault(cusip, {"name": rec.get("nameOfIssuer", ""), "value": 0.0, "shares": 0.0})
        a["value"] += float(rec.get("value") or 0)
        a["shares"] += float(rec.get("sshPrnamt") or 0)
    # 13F values have been in dollars since 2023, but some filers still report thousands.
    # A median "price" under $2 a share gives that away.
    px = sorted(v["value"] / v["shares"] for v in out.values() if v["shares"] > 0)
    if px and px[len(px) // 2] < 2:
        for v in out.values():
            v["value"] *= 1000
    return out


def diff_13f(cur: dict[str, dict], prev: dict[str, dict], top: int = 12) -> dict:
    """Top holdings with quarter-over-quarter action + notable exits."""
    total = sum(v["value"] for v in cur.values()) or 1.0
    rows = []
    for cusip, v in cur.items():
        p = prev.get(cusip)
        if not p or p["shares"] <= 0:
            action, chg = "new", None
        else:
            chg = v["shares"] / p["shares"] - 1
            action = "add" if chg > 0.01 else "cut" if chg < -0.01 else "hold"
        rows.append({"cusip": cusip, "name": v["name"], "value": v["value"],
                     "weight": v["value"] / total, "action": action,
                     "chg": None if chg is None else round(chg, 4)})
    rows.sort(key=lambda r: -r["value"])
    prev_total = sum(v["value"] for v in prev.values()) or 1.0
    exits = sorted(({"cusip": c, "name": p["name"], "weight": p["value"] / prev_total, "action": "exit"}
                    for c, p in prev.items() if c not in cur), key=lambda r: -r["weight"])[:5]
    return {"total_value": total, "positions": len(cur), "top": rows[:top], "exits": exits,
            "actions": {r["cusip"]: r["action"] for r in rows[:50]} | {e["cusip"]: "exit" for e in exits}}


def _info_table_url(cik: int, accession: str) -> str | None:
    acc = accession.replace("-", "")
    idx = http.get_json(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json")
    files = [f["name"] for f in idx["directory"]["item"]
             if f["name"].lower().endswith(".xml") and f["name"].lower() != "primary_doc.xml"]
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{files[0]}" if files else None


def investor_13f(cik: int) -> dict | None:
    sub = http.get_json(f"{SEC}/submissions/CIK{cik:010d}.json")
    rec = sub["filings"]["recent"]
    idx = [i for i, f in enumerate(rec["form"]) if f == "13F-HR"][:2]
    if not idx:
        return None
    tables = []
    for i in idx:
        url = _info_table_url(cik, rec["accessionNumber"][i])
        tables.append(parse_13f(http.get_text(url)) if url else {})
    res = diff_13f(tables[0], tables[1] if len(tables) > 1 else {})
    res.update({"filed": rec["filingDate"][idx[0]], "period": rec["reportDate"][idx[0]]})
    return res


def map_cusips(cusips: list[str], cache: dict[str, str]) -> dict[str, str]:
    """CUSIP -> ticker via OpenFIGI (free tier: 10 ids per call). Mutates and returns cache."""
    todo = [c for c in dict.fromkeys(cusips) if c and c not in cache]
    for i in range(0, len(todo), 10):
        batch = todo[i:i + 10]
        try:
            r = http.request("POST", "https://api.openfigi.com/v3/mapping", retries=1,
                             json=[{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch])
            for c, item in zip(batch, r.json()):
                data = item.get("data") or []
                cache[c] = (data[0].get("ticker") or "").replace("/", "-") if data else ""
        except Exception:
            break  # keep whatever we have; unmapped names still display by issuer name
    return cache


# ----------------------------- SEC fundamentals ----------------------------- #
def ticker_ciks() -> dict[str, int]:
    j = http.get_json("https://www.sec.gov/files/company_tickers.json")
    return {v["ticker"].upper(): int(v["cik_str"]) for v in j.values()}


def _periods(units: list[dict]) -> tuple[dict, dict]:
    """Split XBRL facts into quarterly and annual {end: (start, val)} using latest filings."""
    q, a = {}, {}
    for u in sorted(units, key=lambda x: x.get("filed", "")):
        if not u.get("start") or u.get("val") is None:
            continue
        days = (dt.date.fromisoformat(u["end"]) - dt.date.fromisoformat(u["start"])).days
        if 80 <= days <= 100:
            q[u["end"]] = (u["start"], float(u["val"]))
        elif 350 <= days <= 380:
            a[u["end"]] = (u["start"], float(u["val"]))
    return q, a


def ttm_and_yoy(units: list[dict]) -> dict:
    """Trailing-twelve-month sum and latest-quarter YoY from XBRL duration facts.

    Fiscal Q4 is usually only reported inside the annual figure, so it is
    derived as FY - (Q1+Q2+Q3) when missing.
    """
    q, a = _periods(units)
    for end, (start, val) in a.items():  # derive missing Q4
        if end in q:
            continue
        # Derived quarters carry start=None so they are never counted inside a later year.
        inside = [v for e, (s, v) in q.items() if s and start <= s and e < end]
        if len(inside) == 3:
            q[end] = (None, val - sum(inside))
    ends = sorted(q)
    if not ends:
        return {}
    latest = ends[-1]
    out = {"period": latest, "latest": q[latest][1]}
    if len(ends) >= 4:
        last4 = ends[-4:]
        span = (dt.date.fromisoformat(last4[-1]) - dt.date.fromisoformat(last4[0])).days
        if 250 <= span <= 290:
            out["ttm"] = sum(q[e][1] for e in last4)
    target = dt.date.fromisoformat(latest) - dt.timedelta(days=365)
    prior = [e for e in ends if abs((dt.date.fromisoformat(e) - target).days) <= 20]
    if prior and q[prior[0]][1]:
        base = q[prior[0]][1]
        out["yoy"] = (q[latest][1] - base) / abs(base) * 100
    return out


REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")


def _concept(cik: int, tag: str) -> list[dict]:
    try:
        j = http.get_json(f"{SEC}/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json", retries=1)
    except Exception:
        return []
    units = j.get("units") or {}
    return next(iter(units.values()), [])


def fresh_enough(period: str | None, today: dt.date | None = None, months: int = 15) -> bool:
    """A reported quarter older than this is a discontinued tag, not the company's latest result."""
    if not period:
        return False
    return dt.date.fromisoformat(period) >= (today or dt.date.today()) - dt.timedelta(days=months * 31)


def fundamentals(cik: int, price: float | None) -> dict:
    out: dict = {}
    eps = {}
    for tag in ("EarningsPerShareDiluted", "EarningsPerShareBasic"):
        eps = ttm_and_yoy(_concept(cik, tag))
        if eps and fresh_enough(eps.get("period")):
            break
        eps = {}
    if eps:
        out.update({"eps_period": eps.get("period"), "eps": eps.get("latest"),
                    "eps_ttm": eps.get("ttm"), "eps_yoy": eps.get("yoy")})
        if price and eps.get("ttm") and eps["ttm"] > 0:  # a loss has no meaningful P/E
            out["pe"] = price / eps["ttm"]
    best: dict = {}
    for tag in REVENUE_TAGS:
        rv = ttm_and_yoy(_concept(cik, tag))
        if rv and fresh_enough(rv.get("period")) and rv.get("period", "") > best.get("period", ""):
            best = rv
    if best:
        out.update({"rev_yoy": best.get("yoy"), "revenue_ttm": best.get("ttm")})
    return out


# ----------------------------- SEC Form 4: company insiders' own trades ----------------------------- #
def _txt(node, path: str) -> str:
    el = node.find(path)
    return (el.text or "").strip() if el is not None and el.text else ""


def parse_form4(xml: str) -> list[dict]:
    """Open-market purchases (P) and sales (S) of common stock in one Form 4.

    Grants, gifts, option exercises and tax withholding are left out: they are not decisions
    to buy or sell at the market price. `planned` marks trades under a 10b5-1 plan, which
    are scheduled in advance and say little about the insider's current view.
    """
    root = ET.fromstring(xml)
    owner = _txt(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    role = []
    if rel is not None:
        if _txt(rel, "isDirector") in ("1", "true"):
            role.append("董事")
        if _txt(rel, "isOfficer") in ("1", "true"):
            role.append(_txt(rel, "officerTitle") or "高階主管")
        if _txt(rel, "isTenPercentOwner") in ("1", "true"):
            role.append("10% 大股東")
    planned = _txt(root, "aff10b5One") in ("1", "true")
    out = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _txt(t, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        try:
            shares = float(_txt(t, "transactionAmounts/transactionShares/value") or 0)
            price = float(_txt(t, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        if not shares:
            continue
        out.append({"date": _txt(t, "transactionDate/value")[:10], "owner": owner.title(), "role": "、".join(role) or "內部人",
                    "type": "buy" if code == "P" else "sell", "shares": shares, "value": shares * price, "planned": planned})
    return out


def recent_form4(cik: int, since: str, seen: set[str], limit: int = 20) -> dict[str, list[dict]]:
    """Form 4 filings for one company filed on or after `since` that are not in `seen`:
    {accession number: [trades]} (empty list = filed but nothing to report)."""
    j = http.get_json(f"{SEC}/submissions/CIK{cik:010d}.json")
    r = j["filings"]["recent"]
    out: dict[str, list[dict]] = {}
    for form, acc, filed, doc in zip(r["form"], r["accessionNumber"], r["filingDate"], r["primaryDocument"]):
        if form != "4" or filed < since or acc in seen:
            continue
        raw = doc.split("/")[-1]  # the primary document is an XSL rendering; the raw XML sits beside it
        try:
            xml = http.get_text(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{raw}", retries=1)
            out[acc] = [{**t, "filed": filed} for t in parse_form4(xml)]
        except Exception:  # noqa: BLE001 - one unreadable filing must not cost the rest
            continue
        if len(out) >= limit:
            break
    return out


def insider_summary(trades: list[dict], since: str) -> dict:
    """Per-company totals over trades dated on or after `since`."""
    t = [x for x in trades if x["date"] >= since]
    buys, sells = [x for x in t if x["type"] == "buy"], [x for x in t if x["type"] == "sell"]
    return {"buys": len(buys), "sells": len(sells), "buy_value": sum(x["value"] for x in buys),
            "sell_value": sum(x["value"] for x in sells),
            "sell_unplanned": sum(x["value"] for x in sells if not x["planned"]),
            "buyers": len({x["owner"] for x in buys}), "last": max((x["date"] for x in t), default=None)}
