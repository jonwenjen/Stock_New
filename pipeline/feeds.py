"""Subscribable copies of what changes each week (review 2026-11-14, product-6).

docs/calendar.ics - the two-week calendar (ex-dividend dates, disposition ends, settlements,
                    index rebalances) as all-day events, for phone calendars.
docs/feed.xml     - an Atom feed with one entry per weekly snapshot: the risk score, what moved
                    it, and which picks came in or went out.

Both are rebuilt from files the build has already written, so `python -m pipeline.feeds` can
refresh them on their own.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = DOCS / "data"
SITE = "https://jonwenjen.github.io/Stock_New/"


def _ics_text(s: str) -> str:
    """RFC 5545 text escaping."""
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 73:
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def calendar_ics(events: list[dict], stamp: dt.datetime) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Stock_New//weekly radar//ZH-TW", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:週三選股雷達 行事曆", "X-WR-TIMEZONE:Asia/Taipei"]
    for e in events:
        d = dt.date.fromisoformat(e["date"])
        uid = hashlib.sha1(f"{e['date']}|{e.get('kind')}|{e.get('symbol')}|{e['text']}".encode()).hexdigest()[:20]
        lines += ["BEGIN:VEVENT", f"UID:{uid}@stock-new", f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
                  f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + dt.timedelta(days=1):%Y%m%d}",
                  _fold("SUMMARY:" + _ics_text(f"[{e.get('kind', '')}] {e['text'][:60]}")),
                  _fold("DESCRIPTION:" + _ics_text(e["text"] + "\n資料：週三選股雷達（不構成投資建議）")),
                  "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _entry_text(snap: dict, prev: dict | None) -> str:
    r = snap.get("risk") or {}
    parts = [f"短期回檔風險 {r.get('score', 0):.0f} 分（{r.get('name', '–')}）"]
    if prev and (prev.get("risk") or {}).get("score") is not None:
        parts[0] += f"，上週 {prev['risk']['score']:.0f} 分"
    was = {p["symbol"] for p in (prev or {}).get("picks") or []}
    now = snap.get("picks") or []
    if prev:
        new = [p["name"] for p in now if p["symbol"] not in was]
        out = [p["name"] for p in prev.get("picks") or [] if p["symbol"] not in {x["symbol"] for x in now}]
        parts.append("新進精選：" + ("、".join(new) if new else "無"))
        parts.append("移出精選：" + ("、".join(out) if out else "無"))
    tw = [p["name"] for p in now if p["symbol"].endswith((".TW", ".TWO"))][:5]
    if tw:
        parts.append("台股排名前段：" + "、".join(tw))
    parts.append("排名只比較股票之間的相對強弱，不是漲跌預測，也不構成投資建議。")
    return "。".join(parts)


def atom_feed(snaps: list[dict], updated: dt.datetime) -> str:
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="zh-Hant-TW">',
           "<title>週三選股雷達：本週變化</title>", f'<link href="{SITE}"/>', f'<link rel="self" href="{SITE}feed.xml"/>',
           f"<id>{SITE}</id>", f"<updated>{updated:%Y-%m-%dT%H:%M:%SZ}</updated>", "<author><name>週三選股雷達</name></author>"]
    for i, s in enumerate(snaps[::-1][:26]):
        prev = snaps[::-1][i + 1] if i + 1 < len(snaps) else None
        out += ["<entry>", f"<title>{escape(s['week'])} 本週變化（資料日 {escape(s['date'])}）</title>",
                f'<link href="{SITE}#overview"/>', f"<id>{SITE}#week-{escape(s['week'])}</id>",
                f"<updated>{s['date']}T22:00:00+08:00</updated>",
                f'<content type="text">{escape(_entry_text(s, prev))}</content>', "</entry>"]
    out.append("</feed>")
    return "\n".join(out) + "\n"


def write_feeds() -> dict:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    summary = json.loads((DATA / "summary.json").read_text(encoding="utf-8"))
    (DOCS / "calendar.ics").write_text(calendar_ics(summary.get("calendar") or [], now), encoding="utf-8", newline="")
    snaps = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "history" / "weekly").glob("*.json"))]
    (DOCS / "feed.xml").write_text(atom_feed(snaps, now), encoding="utf-8")
    return {"events": len(summary.get("calendar") or []), "weeks": len(snaps)}


if __name__ == "__main__":
    print(write_feeds())
