#!/usr/bin/env python3
"""
helper_parse_email_to_raw_enhanced.py

Convert Email -> RawBlock list using:
  1) strong quote-depth splitting via leading '>' (RFC-ish)
  2) fallback weak body/quote boundary for Outlook/Exchange variants (no leading '>')

Weak rules (as required):
  - part="body" never runs weak chunking
  - part="quote" and word_count > 1000 may run weak chunking
Trigger segment (for now): "发件人："
"""

import re

try:
    from .helper_sanitize import sanitize_text
except ImportError:  # pragma: no cover
    from helper_sanitize import sanitize_text


_QUOTE_DEPTH_RE = re.compile(r"^(>+)\s*(.*)")
_FROM_ZH_LINE_RE = re.compile(r"(?m)^[\t ]*发件人\s*[:：].*$")


def _word_count(text: str) -> int:
    return len(text.split())


def _split_by_quote_depth(text: str) -> list[tuple[int, str]]:
    """Return ordered list of (depth, text_segment). Depth 0 is body; depth>=1 is quote."""
    buckets: dict[int, list[str]] = {}

    for line in text.splitlines():
        m = _QUOTE_DEPTH_RE.match(line)
        if m:
            depth = len(m.group(1))
            content = m.group(2)
        else:
            depth = 0
            content = line
        buckets.setdefault(depth, []).append(content)

    segments: list[tuple[int, str]] = []
    for depth in sorted(buckets.keys()):
        seg = "\n".join(buckets[depth]).strip()
        if seg:
            segments.append((depth, seg))
    return segments


def _split_body_quote_by_first_sender_zh(text: str) -> tuple[str, str]:
    """
    Fallback: if no RFC '>' quotes detected, try find the first '发件人：' line.
    Return (body, quote). If not found -> (text, "").
    """
    m = _FROM_ZH_LINE_RE.search(text)
    if not m:
        return text.strip(), ""
    return text[: m.start()].strip(), text[m.start() :].strip()


def _split_quote_weak_by_sender_zh(text: str) -> list[str]:
    """
    Weak chunking inside quote: split by repeated '发件人：' markers.
    If none -> [text]
    """
    matches = list(_FROM_ZH_LINE_RE.finditer(text))
    if not matches:
        return [text]

    starts = [m.start() for m in matches]
    parts: list[str] = []

    prev = 0
    for s in starts:
        if s > prev:
            chunk = text[prev:s].strip()
            if chunk:
                parts.append(chunk)
        prev = s

    tail = text[prev:].strip()
    if tail:
        parts.append(tail)

    return parts or [text]


def parse_email_to_raw_blocks(email, email_id):
    text_part = email.get_body(preferencelist=("plain", "html"))
    if not text_part:
        return []

    content = text_part.get_content()
    if not content:
        return []

    # 1) strong split first
    segments = _split_by_quote_depth(content)

    # 2) if no quote detected (only depth=0), do fallback boundary split by "发件人："
    has_quote = any(depth >= 1 for depth, _ in segments)
    if not has_quote and len(segments) == 1 and segments[0][0] == 0:
        body, quote = _split_body_quote_by_first_sender_zh(segments[0][1])
        segments = []
        if body:
            segments.append((0, body))
        if quote:
            segments.append((1, quote))  # mark as quote; do NOT weak-chunk body

    blocks = []
    page = 1

    for depth, text in segments:
        part = "body" if depth == 0 else "quote"

        sub_texts = [text]
        # weak condition chunking: only for big quote segments
        if part == "quote" and _word_count(text) > 500:
            sub_texts = _split_quote_weak_by_sender_zh(text)

        for sub in sub_texts:
            sub = sub.strip()
            if not sub:
                continue
            blocks.append({
                "doc_id": email_id,
                "text": sanitize_text(sub),
                "page": page,
                "source": "mbox",
                "part": part,
            })
            page += 1

    return blocks
