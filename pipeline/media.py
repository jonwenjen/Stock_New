"""Podcasts (RSS) and open-source tool references (GitHub search)."""
from __future__ import annotations

import datetime as dt
import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from . import http

TAG = re.compile(r"<[^>]+>")


def clean(text: str, limit: int = 160) -> str:
    text = html.unescape(TAG.sub(" ", text or ""))
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[_\-=─—~*]{3,}", " ", text)  # show notes love long divider lines
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_feed(xml_text: str, since: dt.datetime, max_items: int = 6) -> list[dict]:
    root = ET.fromstring(xml_text.encode("utf-8"))
    items = []
    for it in root.iter("item"):
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        if when < since:
            continue
        raw = it.findtext("description") or ""
        items.append({"title": clean(it.findtext("title") or "", 140), "date": when.date().isoformat(),
                      "link": (it.findtext("link") or "").strip(), "summary": clean(raw),
                      "_text": clean((it.findtext("title") or "") + " " + raw, 6000)})
        if len(items) >= max_items:
            break
    return items


def build_matchers(names: dict[str, list[str]]) -> dict[str, re.Pattern]:
    """names: symbol -> list of aliases. Latin aliases need word boundaries; CJK do not."""
    out = {}
    for sym, aliases in names.items():
        parts = []
        for a in aliases:
            a = a.strip()
            if len(a) < 2:
                continue
            parts.append(rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])" if a.isascii() else re.escape(a))
        if parts:
            out[sym] = re.compile("|".join(parts))
    return out


def find_mentions(text: str, matchers: dict[str, re.Pattern]) -> list[str]:
    return [sym for sym, rx in matchers.items() if rx.search(text)]


def podcasts(feeds: list[dict], matchers: dict[str, re.Pattern], days: int = 14,
             now: dt.datetime | None = None) -> tuple[list[dict], dict[str, int], list[str]]:
    """Returns (shows, mention counts per symbol, errors)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=days)
    shows, counts, errors = [], {}, []
    for f in feeds:
        try:
            eps = parse_feed(http.get_text(f["feed"], timeout=60, retries=1), since)
        except Exception as e:
            errors.append(f"{f['name']}: {e}")
            continue
        for ep in eps:
            ep["mentions"] = find_mentions(ep.pop("_text"), matchers)
            for s in ep["mentions"]:
                counts[s] = counts.get(s, 0) + 1
        shows.append({"name": f["name"], "lang": f["lang"], "episodes": eps})
    return shows, counts, errors


def github_tools() -> list[dict]:
    """Popular open-source investing / AI-finance projects, for the reference section."""
    seen, out = set(), []
    for q in ("stock analysis AI agent", "quantitative trading", "台股", "financial LLM"):
        try:
            j = http.get_json("https://api.github.com/search/repositories",
                              params={"q": q, "sort": "stars", "order": "desc", "per_page": 6},
                              headers={"Accept": "application/vnd.github+json"}, retries=1)
        except Exception:
            continue
        for r in j.get("items", []):
            if r["full_name"] in seen:
                continue
            seen.add(r["full_name"])
            out.append({"name": r["full_name"], "url": r["html_url"], "stars": r["stargazers_count"],
                        "desc": clean(r.get("description") or "", 120), "pushed": (r.get("pushed_at") or "")[:10]})
    return sorted(out, key=lambda r: -r["stars"])[:16]
