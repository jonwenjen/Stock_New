"""Validate docs/data/ai.json - the commentary Claude writes during the weekly run.

    python -m pipeline.check_ai

The build never writes this file; the weekly Claude session does, from
.cache/digest.json. This check makes sure what gets pushed matches the data
it claims to describe and has every part the site expects.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "docs" / "data"
MARKET_HEADINGS = 4  # 盤勢重點 / 籌碼面 / 展望 / 部位與風險

# Wording the site retired. 2026-11-14: no stock-level up probability, no price target, no instructions
# about holdings. 2026-11-14d (review of outside suggestions, H): no direction calls or trading
# instructions at all - the commentary describes the data, the reader decides.
BANNED = ("上漲機率", "一月目標", "風險報酬比", "建議降低持股", "建議持股",
          "偏多", "偏空", "看多", "看空", "觀望", "看好", "宜等", "宜減碼", "宜加碼", "等止穩", "追高", "逢低",
          "布局", "佈局", "上車", "建議", "只適合短線")
ALLOWED = ("不構成投資建議", "投資建議")  # the disclaimer itself may name advice

# A number written after one of these phrases must equal the record's field (after rounding to the
# decimals written). Percent fields only bind a number followed by "%".
BIND = (("近一週", "chg5d", 100, True), ("近一月", "r20", 100, True), ("近一季", "r60", 100, True),
        ("今年", "ytd", 100, True), ("EPS 年增", "eps_yoy", 1, True), ("營收年增", "rev_yoy", 1, True),
        ("本益比", "pe", 1, False), ("RSI", "rsi", 1, False))
NUM = re.compile(r"[-+−]?\d+(?:\.\d+)?")


def stale_numbers(text: str, rec: dict) -> list[str]:
    """Numbers in a note that do not match the same row's data (review 2026-11-14d, H: notes written
    before a rebuild kept last week's figures - 16 of 28 numbers were wrong)."""
    errs = []
    for phrase, key, scale, pct in BIND:
        if rec.get(key) is None:
            continue
        for m in re.finditer(re.escape(phrase), text):
            tail = text[m.end(): m.end() + 10]
            n = re.search(NUM.pattern + (r"\s*%" if pct else ""), tail)
            if not n:
                continue
            raw = NUM.match(n.group()).group().replace("−", "-")
            dec = len(raw.split(".")[1]) if "." in raw else 0
            want = round(abs(float(rec[key]) * scale), dec)
            if abs(abs(float(raw)) - want) > 0.5 * 10 ** -dec + 1e-9:
                errs.append(f"「{phrase}{tail[:n.end()]}」 but the data says {want:g}")
    for m in re.finditer(r"(\d+)\s*集", text):
        if rec.get("mentions") is not None and int(m.group(1)) != rec["mentions"]:
            errs.append(f"「{m.group()}」 but the data says {rec['mentions']} 集")
    return errs


def validate(ai: dict, data_date: str, hot_symbols: list[str], need_fund: bool = False,
             digest_sha: str | None = None, records: dict[str, dict] | None = None) -> list[str]:
    """Return a list of problems; empty means the file is fit to publish."""
    errs: list[str] = []
    if ai.get("data_date") != data_date:
        errs.append(f"data_date is {ai.get('data_date')!r}, the build's is {data_date!r}")
    if digest_sha and ai.get("digest_sha") != digest_sha:  # written from this build's digest, not an earlier one
        errs.append(f"digest_sha is {ai.get('digest_sha')!r}, this build's digest is {digest_sha!r}")
    market = ai.get("market") or ""
    if len(market) < 250:
        errs.append("market commentary is missing or too short (< 250 characters)")
    if market.count("### ") < MARKET_HEADINGS:
        errs.append(f"market commentary needs {MARKET_HEADINGS} sections, each starting with '### '")
    pod = ai.get("podcast") or {}
    stocks = pod.get("stocks") or {}
    if hot_symbols and len(pod.get("overview") or "") < 60:
        errs.append("podcast.overview is missing or too short")
    for sym in hot_symbols[:5]:
        if sym not in stocks:
            errs.append(f"podcast.stocks lacks the hot name {sym}")
    for sym, text in stocks.items():
        if sym not in hot_symbols:
            errs.append(f"podcast.stocks has {sym}, which is not in this week's hot list")
        elif not 30 <= len(text or "") <= 400:
            errs.append(f"podcast.stocks[{sym}] should be 30-400 characters")
        elif records and sym in records:
            errs += [f"podcast.stocks[{sym}]: {e}" for e in stale_numbers(text, records[sym])]
    if len(ai.get("etf") or "") < 120:
        errs.append("etf commentary is missing or too short (< 120 characters)")
    if need_fund and len(ai.get("fund") or "") < 150:
        errs.append("fund commentary is missing or too short (< 150 characters)")
    blob = json.dumps({k: v for k, v in ai.items() if k not in ("data_date", "written_at", "digest_sha")},
                      ensure_ascii=False)
    for ok_phrase in ALLOWED:
        blob = blob.replace(ok_phrase, "")
    for phrase in BANNED:
        if phrase in blob:
            errs.append(f"retired wording found: {phrase}")
    for marker in ("TODO", "TBD", "XXX"):
        if marker in blob:
            errs.append(f"placeholder text found: {marker}")
    return errs


def main() -> int:
    try:
        ai = json.loads((DATA / "ai.json").read_text(encoding="utf-8"))
        summary = json.loads((DATA / "summary.json").read_text(encoding="utf-8"))
        pod = json.loads((DATA / "podcasts.json").read_text(encoding="utf-8"))
        rows = json.loads((DATA / "universe.json").read_text(encoding="utf-8"))
        rows += json.loads((DATA / "etf.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"cannot read data files: {e}")
        return 1
    records = {r["symbol"]: r for r in rows}
    for h in pod.get("hot", []):  # the hot list counts mentions over the same two weeks
        records.setdefault(h["symbol"], {}).update({"mentions": h.get("mentions")})
    errs = validate(ai, summary["data_date"], [h["symbol"] for h in pod.get("hot", [])],
                    need_fund=bool((summary.get("counts") or {}).get("fund")),
                    digest_sha=summary.get("digest_sha"), records=records)
    for e in errs:
        print("FAIL", e)
    if not errs:
        print(f"ai.json OK for data date {summary['data_date']}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
