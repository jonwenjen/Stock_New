"""Optional AI commentary (Claude). Runs only when ANTHROPIC_API_KEY is configured.

The rule-based narrative in build.py is always produced; this adds a second,
LLM-written reading of the very same numbers. It never invents data: the model
only sees the JSON digest passed in.
"""
from __future__ import annotations

import json
import os

MODEL = "claude-opus-5-5"

SYSTEM = """你是一位嚴謹的台股與美股研究員，為一個每週三更新的投資儀表板撰寫「本週 AI 觀點」。
讀者是一般投資人，用繁體中文（台灣用語）。

寫作規則：
- 只能使用使用者訊息中 JSON 提供的數據；不要引用其中沒有的數字、新聞或事件。
- 先講結論。依序寫四段，每段一個小標題：「本週盤勢重點」「籌碼面（外資與融資）」「未來一週／一月／一季展望」「操作建議與風險」。
- 每段 2 到 4 句，總長 350 到 500 字。具體引用數據（例如外資近5日買賣超金額、融資餘額變化、指數與均線位置）。
- 展望是機率判斷而非保證，請明確說出不確定之處與可能讓判斷失效的情境。
- 不使用 Markdown 語法以外的格式；小標題用「### 」開頭。不要寫免責聲明，網站已有。"""


def commentary(digest: dict) -> str | None:
    """Return markdown commentary, or None when no key / SDK / on any API failure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    client = anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # re-run on a fallback model if the request is declined
            output_config={"effort": "medium"},
            system=SYSTEM,
            messages=[{"role": "user", "content":
                       "以下是本週資料摘要（JSON）。請依規則撰寫本週 AI 觀點。\n\n"
                       + json.dumps(digest, ensure_ascii=False, indent=1)}],
        )
    except anthropic.RateLimitError as e:
        print(f"[ai] rate limited: {e.message}")
        return None
    except anthropic.APIStatusError as e:
        print(f"[ai] API error {e.status_code}: {e.message}")
        return None
    except anthropic.APIConnectionError as e:
        print(f"[ai] connection error: {e}")
        return None
    if resp.stop_reason == "refusal":
        print("[ai] request declined; keeping rule-based narrative only")
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text or None
