"""
Responsibility:
Text processing helpers used across email and LLM flows: normalize values to strings, remove `<think>` blocks from model output, and extract message bodies from `email.message.Message`.

Used by:
* rag/email_01_mbox_to_chunks.py
* helper/utils_llm.py

Pipelines:
- format_text -> normalize_output -> strip_think
- walk_message -> decode_part -> html_to_text -> build_tasks

Invariants:
- `_strip_think` removes matched `<think>...</think>` regions while preserving non-think text.
- `extract_email_body` prefers `text/plain` when available and falls back to `text/html`.
- `extract_email_body_tasks` returns at most one task and truncates body text to `max_len`.

Out of scope:
- Attachment extraction (handled elsewhere).
- Chunking and JSONL persistence.
"""

import re
from email.message import Message
from typing import Any, List, Optional, Tuple

from bs4 import BeautifulSoup


def _format_text(v: Any) -> str:
    """
    Purpose:
    Convert a value to a normalized text string.

    Inputs:
    - v: Any value, typically a string-like or None.

    Outputs:
    - Normalized string with surrounding whitespace stripped; returns `""` for `None`.

    Side effects:
    - None.

    Failure modes:
    - None.
    """
    if v is None:
        return ""
    return v.strip() if isinstance(v, str) else str(v).strip()


_THINK_TAG = re.compile(r"<\s*(/?)\s*think\b[^>]*>", re.IGNORECASE)


def _strip_think(text: str) -> str:
    """
    Purpose:
    Remove `<think>...</think>` blocks from a string while preserving non-think content.

    Inputs:
    - text: Input string.

    Outputs:
    - String with think blocks removed; returns the original string when no tags are present.

    Side effects:
    - None.

    Failure modes:
    - None.
    """
    if not text or "<" not in text:
        return text

    depth, last, out = 0, 0, []

    for m in _THINK_TAG.finditer(text):
        s, e = m.span()
        closing = bool(m.group(1))

        if depth == 0:
            out.append(text[last:s])

        if not closing:
            if depth == 0:
                last = e
            depth += 1
        else:
            if depth > 0:
                depth -= 1
                if depth == 0:
                    last = e
            else:
                out.append(m.group(0))
                last = e

    if depth == 0:
        out.append(text[last:])

    return "".join(out)

def _normalize_output(content: Any) -> str:
    """
    Purpose:
    Normalize an LLM response payload to a clean string and remove think blocks.

    Inputs:
    - content: Either a string, a list of objects/dicts with `text`, or a falsy value.

    Outputs:
    - Normalized string with surrounding whitespace stripped and think blocks removed.

    Side effects:
    - None.

    Failure modes:
    - None.
    """
    if isinstance(content, str):
        text = content.strip()
    elif not content:
        text = ""
    else:
        parts: List[str] = []
        for c in content:
            t = getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else None)
            if t:
                parts.append(str(t))
        text = "\n".join(parts).strip()

    return _strip_think(text)


def extract_email_body(msg: Message) -> str:
    """
    Purpose:
    Extract an email body from a message, preferring `text/plain` and falling back to `text/html`.

    Inputs:
    - msg: Email message object.

    Outputs:
    - Extracted body text as a string (may be empty).

    Side effects:
    - Parses HTML via BeautifulSoup when `text/html` is selected.

    Failure modes:
    - Returns `""` when no suitable body part is found or decoding yields empty text.
    """

    def _decode_part(part: Message) -> Optional[str]:
        """
        Purpose:
        Decode a message part payload to a string using its declared charset with fallback.

        Inputs:
        - part: Message part.

        Outputs:
        - Decoded string, or `None` when no payload is available.

        Side effects:
        - None.

        Failure modes:
        - Falls back to UTF-8 decode with replacement on decode errors.
        """

        payload = part.get_payload(decode=True)
        if payload is None:
            raw_payload = part.get_payload(decode=False)
            return raw_payload if isinstance(raw_payload, str) else None
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except Exception:
            return payload.decode("utf-8", errors="replace")

    def _html_to_text(content: str) -> str:
        """
        Purpose:
        Convert HTML content to plain text.

        Inputs:
        - content: HTML string.

        Outputs:
        - Plain text extracted via BeautifulSoup.

        Side effects:
        - Parses HTML.

        Failure modes:
        - Propagates exceptions raised by BeautifulSoup on malformed inputs.
        """

        soup = BeautifulSoup(content, "html.parser")
        return soup.get_text(separator="\n", strip=True)

    get_body = getattr(msg, "get_body", None)
    if callable(get_body):
        plain = msg.get_body(preferencelist=("plain",))
        if plain:
            text = plain.get_content()
            if text and text.strip():
                return text.strip()
        html = msg.get_body(preferencelist=("html",))
        if html:
            html_content = html.get_content()
            if html_content:
                return _html_to_text(html_content)
        return ""

    preferred: list[Message] = []
    others: list[Message] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        if part.get_content_type() == "text/plain" and "attachment" not in disp:
            preferred.append(part)
        else:
            others.append(part)

    for part in preferred + others:
        text = _decode_part(part)
        if not text:
            continue
        text = text.strip()
        if not text:
            continue
        return _html_to_text(text) if part.get_content_type() == "text/html" else text

    return ""


def extract_email_body_tasks(
    msg: Message, base_meta: dict, max_len: int
) -> List[Tuple[str, dict]]:
    """
    Purpose:
    Extract an email body and wrap it as a single `(text, metadata)` task tuple.

    Inputs:
    - msg: Email message.
    - base_meta: Base metadata to merge into the task metadata dict.
    - max_len: Maximum length (characters) to keep from the extracted body.

    Outputs:
    - A list with 0 or 1 `(text, metadata)` tuples.

    Side effects:
    - Calls `extract_email_body`.

    Failure modes:
    - Returns `[]` when the extracted body is empty.
    """

    body = extract_email_body(msg)
    if not body.strip():
        return []
    text = body[:max_len]
    return [
        (
            text,
            {
                **base_meta,
                "part": "body",
                "file_type": "text",  # ✅ 可選
                "attachment": None,
            },
        )
    ]


__all__ = ["_format_text", "_normalize_output", "extract_email_body", "extract_email_body_tasks"]
