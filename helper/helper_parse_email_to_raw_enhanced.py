#!/usr/bin/env python3
"""
helper_parse_email_to_raw.py
Responsibility:
Convert Email -> RawBlock list using quote-depth based splitting.
This is robust, format-agnostic, and does not depend on guessing headers.
"""

import re

try:
    from .helper_sanitize import sanitize_text
except ImportError:  # pragma: no cover
    from helper_sanitize import sanitize_text


_QUOTE_DEPTH_RE = re.compile(r"^(>+)\s*(.*)")

_ON_WROTE_RE = re.compile(r"^\s*On .{0,200}\bwrote\s*:\s*$")
_HDR_RE = re.compile(r"^\s*(From|Sent|Date|To|Cc|Subject)\s*:\s*", re.I)


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


def _split_quote_by_on_wrote(text: str) -> list[str]:
    """
    Minimal, robust-enough step:
    Split quote segment by 'On ... wrote:' anchors only, with light confirmation:
      - anchor is followed by a blank line; OR
      - within next 2 lines, we see a typical header line (From/Sent/Date/To/Cc/Subject).
    Otherwise, don't split.
    """
    t = (text or "").strip()
    if not t:
        return []

    lines = t.splitlines()
    if len(lines) < 3:
        return [t]

    cuts = [0]
    last_cut = 0
    n = len(lines)

    for i in range(1, n - 1):
        if i - last_cut < 2:
            continue
        if not _ON_WROTE_RE.match(lines[i]):
            continue

        # confirm: next line blank OR header soon after
        next1 = lines[i + 1].strip()
        next2 = lines[i + 2].strip() if i + 2 < n else ""
        ok = (next1 == "") or _HDR_RE.match(next1) or _HDR_RE.match(next2)

        if ok:
            cuts.append(i)
            last_cut = i

    if len(cuts) == 1:
        return [t]

    cuts.append(n)
    out: list[str] = []
    for a, b in zip(cuts, cuts[1:]):
        seg = "\n".join(lines[a:b]).strip()
        if seg:
            out.append(seg)
    return out if out else [t]


def parse_email_to_raw_blocks(email, email_id):
    text_part = email.get_body(preferencelist=("plain", "html"))
    if not text_part:
        return []

    content = text_part.get_content()
    if not content:
        return []

    segments = _split_by_quote_depth(content)

    blocks = []
    page = 1

    for depth, text in segments:
        if depth == 0:
            blocks.append({
                "doc_id": email_id,
                "text": sanitize_text(text),
                "page": page,
                "source": "mbox",
                "part": "body",
            })
            page += 1
            continue

        for sub in _split_quote_by_on_wrote(text):
            blocks.append({
                "doc_id": email_id,
                "text": sanitize_text(sub),
                "page": page,
                "source": "mbox",
                "part": "quote",
            })
            page += 1

    return blocks
