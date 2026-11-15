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


SPLIT = re.compile(r"[\n\r。！!？?｜|：:；;]+|\s{2,}")
URL = re.compile(r"https?://\S+|www\.\S+")
# Link labels of podcast platforms: "Apple Podcasts" in a footer is not a mention of Apple (AAPL).
PLATFORM = re.compile(r"Apple\s*Podcasts?|Google\s*Podcasts?|Spotify|YouTube|KKBOX|SoundOn|Firstory|Pocket\s*Casts|"
                      r"Overcast|Castbox|Podcast\s*App|Apple\s*Music", re.I)
CHAPTER = re.compile(r"^[\(\[]?(\d{1,2}:\d{2}(?::\d{2})?)[\)\]]?\s*[-–—.]?\s*(.+)$")
AD = re.compile(r"贊助|業配|優惠|折扣碼|課程|廣告|privacy information|Hosting provided", re.I)
# Theme words, fixed before any count was looked at (review 2026-11-14d, G2): weekly hits are archived
# and judged after 26 weeks; nothing is shown until then.
THEMES = ["CoWoS", "CPO", "矽光子", "ASIC", "HBM", "散熱", "機器人", "重電", "降息", "關稅"]


def _lines(raw: str) -> list[str]:
    text = PLATFORM.sub(" ", URL.sub(" ", html.unescape(TAG.sub("\n", raw or ""))))
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n") if ln.strip()]


def chunks(raw: str) -> list[str]:
    """Show notes cut into sentence-sized pieces (HTML, links and platform labels removed) for
    boilerplate detection and matching. Chapter lines are kept whole, so "06:54 解析台積電…" is not cut at
    its colon."""
    out = []
    for ln in _lines(raw):
        m = CHAPTER.match(ln)
        parts = [f"{m[1]} {m[2]}"] if m else SPLIT.split(ln)
        out += [c for c in (clean(part, 400) for part in parts) if len(c) >= 2]
    return out


def chapters(raw: str) -> list[tuple[str, str]]:
    """(timestamp, title) for each chapter line of the notes, e.g. ("06:54", "解析台積電重訊…")."""
    return [(m[1], clean(m[2], 60)) for ln in _lines(raw) for m in [CHAPTER.match(ln)] if m and len(m[2].strip()) >= 2]


def parse_feed(xml_text: str, since: dt.datetime | None, max_items: int | None = 6) -> list[dict]:
    """Episodes newest first. since=None and max_items=None read the whole back catalogue (RSS feeds keep
    every episode, which is what the post-mention history is built from)."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    items = []
    for it in root.iter("item"):
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        if since is not None and when < since:
            continue
        raw = it.findtext("description") or ""
        items.append({"title": clean(PLATFORM.sub(" ", it.findtext("title") or ""), 140), "date": when.date().isoformat(),
                      "link": (it.findtext("link") or "").strip(), "_raw": raw, "_chunks": chunks(raw)})
        if max_items is not None and len(items) >= max_items:
            break
    return items


def episode_texts(episodes: list[dict]) -> list[str]:
    """Title + the parts of each episode's notes that are unique to that episode.

    Sponsor blocks and "follow us on ..." footers repeat in every episode of a show
    and would otherwise count as a mention each time, so any piece that appears in
    two or more of the show's recent episodes is dropped.
    """
    seen: dict[str, int] = {}
    for ep in episodes:
        for c in set(ep["_chunks"]):
            seen[c] = seen.get(c, 0) + 1
    return [ep["title"] + " 。 " + " 。 ".join(c for c in ep["_chunks"] if seen[c] == 1) for ep in episodes]


def summary_text(ep: dict, mode: str, unique: set[str]) -> str:
    """What the episode list shows under a title (review 2026-11-14d, H): 'title' shows nothing (shows
    whose notes are all sponsor reads), 'chapters' only the chapter lines without sponsor chapters,
    'text' the notes unique to the episode without sponsor or platform lines (hidden under 20 characters)."""
    if mode == "title":
        return ""
    if mode == "chapters":
        cs = [f"{at} {t}" for at, t in chapters(ep["_raw"]) if not AD.search(t)]
        return clean("・".join(cs), 160) if cs else ""
    text = "。".join(c for c in ep["_chunks"] if c in unique and not AD.search(c) and not CHAPTER.match(c))
    text = clean(text, 160)
    return text if len(text) >= 20 else ""


def locate(rx: re.Pattern, ep: dict, unique: set[str]) -> dict:
    """Where a mention sits: the title, a chapter line (with its timestamp) or a piece of the notes."""
    if rx.search(ep["title"]):
        return {"kind": "title", "at": None, "text": clean(ep["title"], 60)}
    for at, t in chapters(ep["_raw"]):
        if rx.search(t):
            return {"kind": "chapter", "at": at, "text": t}
    hit = next((c for c in ep["_chunks"] if c in unique and rx.search(c)), "")
    return {"kind": "notes", "at": None, "text": clean(hit, 60)}


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


def mention_rows(eps: list[dict], matchers: dict[str, re.Pattern], block: int = 6) -> list[tuple[str, str]]:
    """(episode date, symbol) for every mention in a show's episodes, newest first. Boilerplate is
    found within blocks of `block` neighbouring episodes, the way the weekly list finds it."""
    out = []
    for i in range(0, len(eps), block):
        part = eps[i:i + block]
        for ep, text in zip(part, episode_texts(part)):
            out += [(ep["date"], s) for s in find_mentions(text, matchers)]
    return out


def theme_rows(eps: list[dict], block: int = 6) -> list[tuple[str, str]]:
    """(episode date, theme) for every fixed theme word in an episode's title and unique notes."""
    out = []
    for i in range(0, len(eps), block):
        part = eps[i:i + block]
        for ep, text in zip(part, episode_texts(part)):
            out += [(ep["date"], t) for t in THEMES if t.lower() in text.lower()]
    return out


def podcasts(feeds: list[dict], matchers: dict[str, re.Pattern], days: int = 14,
             now: dt.datetime | None = None, history_since: dict[str, str] | None = None) -> dict:
    """The last `days` of each show for the page, plus the mention rows of older episodes for the history.

    history_since: show name -> the newest episode date already in the history file; episodes after
    it (all of them for a show not in the file) are matched for the history. Returns {"shows",
    "mentions" (counts per symbol over `days`), "errors", "events" [(date, show, symbol)], "themes"
    [(date, show, theme)], "span" {show: [first date, last date] of the feed}}."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=days)
    history_since = history_since or {}
    shows, counts, errors, events, themes, span = [], {}, [], [], [], {}
    for f in feeds:
        try:
            allx = parse_feed(http.get_text(f["feed"], timeout=90, retries=1), None, None)
        except Exception as e:
            errors.append(f"{f['name']}: {e}")
            continue
        allx.sort(key=lambda e: e["date"], reverse=True)
        if allx:
            span[f["name"]] = [allx[-1]["date"], allx[0]["date"]]
        cut = history_since.get(f["name"])
        hist = [ep for ep in allx if cut is None or ep["date"] >= cut]
        events += [(d, f["name"], s) for d, s in mention_rows(hist, matchers)]
        themes += [(d, f["name"], t) for d, t in theme_rows(hist)]
        eps = [ep for ep in allx if ep["date"] >= since.date().isoformat()][:6]
        seen: dict[str, int] = {}
        for ep in eps:
            for c in set(ep["_chunks"]):
                seen[c] = seen.get(c, 0) + 1
        unique = {c for c, n in seen.items() if n == 1}
        mode = f.get("notes", "text")
        for ep, text in zip(eps, episode_texts(eps)):
            ep["mentions"] = find_mentions(text, matchers)
            ep["where"] = {s: locate(matchers[s], ep, unique) for s in ep["mentions"]}
            ep["summary"] = summary_text(ep, mode, unique)
            for s in ep["mentions"]:
                counts[s] = counts.get(s, 0) + 1
        for ep in eps:
            ep.pop("_chunks", None)
            ep.pop("_raw", None)
        shows.append({"name": f["name"], "lang": f["lang"], "episodes": eps})
    return {"shows": shows, "mentions": counts, "errors": errors, "events": events, "themes": themes, "span": span}


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
