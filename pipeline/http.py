"""HTTP helper: per-host throttling + retries. Every data source goes through here."""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import requests

# One honest, self-identifying User-Agent for every source. If a site throttles
# or refuses it, we back off and fall back to stored data - never disguise the client.
BOT_UA = "Mozilla/5.0 (compatible; StockNewBot/1.0; weekly research dashboard; low volume)"
# SEC's fair-access policy wants "name contact-email". Set SEC_USER_AGENT to your own
# contact (e.g. a repository variable); the default is a generic placeholder.
SEC_UA = os.environ.get("SEC_USER_AGENT") or "StockNew-research-bot contact@example.com"

# Minimum seconds between two requests to the same host.
HOST_INTERVAL = {
    "www.twse.com.tw": 5.0,  # TWSE blocks clients that go much faster
    "openapi.twse.com.tw": 1.0,
    "www.tpex.org.tw": 1.0,
    "query1.finance.yahoo.com": 1.5,
    "query2.finance.yahoo.com": 1.2,
    "data.sec.gov": 0.15,
    "www.sec.gov": 0.15,
    "api.openfigi.com": 2.6,
    "itunes.apple.com": 3.0,
}

_last_call: dict[str, float] = {}
_session = requests.Session()


def _throttle(host: str) -> None:
    gap = HOST_INTERVAL.get(host, 0.2)
    wait = _last_call.get(host, 0) + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def request(method: str, url: str, *, retries: int = 3, backoff: float = 4.0,
            timeout: float = 40, headers: dict | None = None, **kw) -> requests.Response:
    """Throttled request with retries. Raises on final failure."""
    host = urlparse(url).netloc
    hdrs = {"User-Agent": SEC_UA if host.endswith("sec.gov") else BOT_UA,
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}
    hdrs.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries + 1):
        _throttle(host)
        try:
            r = _session.request(method, url, headers=hdrs, timeout=timeout,
                                 allow_redirects=False, **kw)
            # TWSE answers 307 -> "security" page when it is rate limiting.
            if r.status_code in (301, 302, 307, 308) and host != "www.twse.com.tw":
                loc = r.headers.get("location")
                if loc:
                    r = _session.request(method, requests.compat.urljoin(url, loc),
                                         headers=hdrs, timeout=timeout, **kw)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} for {url}")
            if r.status_code in (400, 401, 403, 404):
                break
        except requests.RequestException as e:  # network error -> retry
            last = e
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    raise last or RuntimeError(f"request failed: {url}")


def get_json(url: str, **kw):
    return request("GET", url, **kw).json()


def get_text(url: str, **kw) -> str:
    r = request("GET", url, **kw)
    r.encoding = r.encoding or "utf-8"
    return r.text
