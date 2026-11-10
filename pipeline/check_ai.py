"""Validate docs/data/ai.json - the commentary Claude writes during the weekly run.

    python -m pipeline.check_ai

The build never writes this file; the weekly Claude session does, from
.cache/digest.json. This check makes sure what gets pushed matches the data
it claims to describe and has every part the site expects.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "docs" / "data"
MARKET_HEADINGS = 4  # 盤勢重點 / 籌碼面 / 展望 / 操作建議與風險


def validate(ai: dict, data_date: str, hot_symbols: list[str]) -> list[str]:
    """Return a list of problems; empty means the file is fit to publish."""
    errs: list[str] = []
    if ai.get("data_date") != data_date:
        errs.append(f"data_date is {ai.get('data_date')!r}, the build's is {data_date!r}")
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
    if len(ai.get("etf") or "") < 120:
        errs.append("etf commentary is missing or too short (< 120 characters)")
    blob = json.dumps(ai, ensure_ascii=False)
    for marker in ("TODO", "TBD", "XXX"):
        if marker in blob:
            errs.append(f"placeholder text found: {marker}")
    return errs


def main() -> int:
    try:
        ai = json.loads((DATA / "ai.json").read_text(encoding="utf-8"))
        summary = json.loads((DATA / "summary.json").read_text(encoding="utf-8"))
        pod = json.loads((DATA / "podcasts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"cannot read data files: {e}")
        return 1
    errs = validate(ai, summary["data_date"], [h["symbol"] for h in pod.get("hot", [])])
    for e in errs:
        print("FAIL", e)
    if not errs:
        print(f"ai.json OK for data date {summary['data_date']}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
