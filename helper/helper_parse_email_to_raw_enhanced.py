#!/usr/bin/env python3
"""
helper_parse_email_to_raw_enhanced.py
Responsibility:
Convert Email -> RawBlock list using quote-depth based splitting.

Enhanced:
Apply pluggable QUOTE_SPLIT_STRATEGIES on quote segments.
Phases:
- Phase 1: split by "On ... wrote:"
- Phase 2: split by header blocks (From/Date/Sent/To/Subject/Cc)
- Phase 3: split by forwarded markers (incl. Chinese "-------- 轉寄郵件 --------")
"""

import re

try:
    from .helper_sanitize import sanitize_text
except ImportError:  # pragma: no cover
    from helper_sanitize import sanitize_text


# ------------------------------------------------------------
# Quote split strategies
# ------------------------------------------------------------

# Phase 1: on_wrote
_ON_WROTE_RE = re.compile(r"^\s*On .{0,200}\bwrote\s*:\s*$")
_HDR_RE = re.compile(r"^\s*(From|Sent|Date|To|Cc|Subject)\s*:\s*", re.I)


def _split_quote_by_on_wrote(text: str) -> list[str]:
    """
    Split quote segment by 'On ... wrote:' anchors only, with light confirmation:
      - anchor is followed by a blank line; OR
      - within next 2 lines, we see a typical header line.
    Otherwise, no split.
    """
    t = (text or "").strip()
    if not t:
        return []

    lines = t.splitlines()
    n = len(lines)
    if n < 3:
        return [t]

    cuts = [0]
    last_cut = 0

    for i in range(1, n - 1):
        if i - last_cut < 2:
            continue
        if not _ON_WROTE_RE.match(lines[i]):
            continue

        next1 = lines[i + 1].strip()
        next2 = lines[i + 2].strip() if i + 2 < n else ""
        ok = (next1 == "") or bool(_HDR_RE.match(next1)) or bool(_HDR_RE.match(next2))

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


# Phase 2: header_block
_HDR_KEY_RE = re.compile(r"^\s*(From|Date|Sent|To|Subject|Cc)\s*:\s*", re.I)


def _split_quote_by_header_block(text: str, *, min_keys: int = 3, lookahead: int = 6) -> list[str]:
    """
    Split quote segment by detecting RFC-like header blocks.

    Rule:
    - A new segment starts at a line where, within a lookahead window,
      >= min_keys distinct header keys appear.
    - If no boundary detected, return [text] unchanged.
    """
    t = (text or "").strip()
    if not t:
        return []

    lines = t.splitlines()
    n = len(lines)
    if n < lookahead:
        return [t]

    cuts = [0]
    last_cut = 0

    for i in range(1, n):
        if i - last_cut < lookahead:
            continue

        keys = set()
        for j in range(i, min(i + lookahead, n)):
            m = _HDR_KEY_RE.match(lines[j])
            if m:
                keys.add(m.group(1).lower())

        if len(keys) >= min_keys:
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


# Phase 3: forwarded markers (only add this pattern family)
_FWD_LINE_RE = re.compile(
    r"^\s*(?:-+\s*)?(?:"
    r"轉寄郵件|转寄邮件|"                      # Chinese
    r"Forwarded message|Forwarded Message|"    # English common
    r"Begin forwarded message"                 # Apple Mail common
    r")\s*(?:-+)?\s*:?\s*$",
    re.I,
)

def _split_quote_by_forward_email(text: str) -> list[str]:
    """
    Split quote segment by forwarded-email separator lines, e.g.:
      - "-------- 轉寄郵件 --------"
      - "-------- 转寄邮件 --------"
      - tolerant to number of dashes/spaces

    If no boundary detected, return [text] unchanged.
    """
    t = (text or "").strip()
    if not t:
        return []

    lines = t.splitlines()
    n = len(lines)
    if n < 2:
        return [t]

    cuts = [0]
    last_cut = 0

    for i in range(1, n):
        if i - last_cut < 2:
            continue
        if _FWD_LINE_RE.match(lines[i]):
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


# Strategy registry
QUOTE_SPLIT_STRATEGIES = [
    _split_quote_by_on_wrote,
    _split_quote_by_header_block,
    _split_quote_by_forward_email,
]


def _apply_quote_split_strategies(text: str) -> list[str]:
    """Apply quote split strategies sequentially. Each strategy may further split segments."""
    segments = [text]
    for strat in QUOTE_SPLIT_STRATEGIES:
        next_segments: list[str] = []
        for seg in segments:
            parts = strat(seg)
            next_segments.extend(parts if parts else [])
        segments = next_segments
    return segments


# ------------------------------------------------------------
# Quote-depth splitter (phase 0)
# ------------------------------------------------------------

_QUOTE_DEPTH_RE = re.compile(r"^(>+)\s*(.*)")


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


# ------------------------------------------------------------
# Main API (drop-in)
# ------------------------------------------------------------

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

    def _emit(part: str, txt: str):
        nonlocal page
        blocks.append({
            "doc_id": email_id,
            "text": sanitize_text(txt),
            "page": page,
            "source": "mbox",
            "part": part,
        })
        page += 1

    for depth, text in segments:
        if depth == 0:
            _emit("body", text)
            continue

        for sub in _apply_quote_split_strategies(text):
            _emit("quote", sub)

    return blocks
