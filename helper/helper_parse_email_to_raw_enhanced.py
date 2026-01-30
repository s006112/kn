#!/usr/bin/env python3
"""
helper_parse_email_to_raw_enhanced.py
Responsibility:
Convert Email -> RawBlock list using quote-depth based splitting.

Enhanced (minimal step):
Apply pluggable QUOTE_SPLIT_STRATEGIES on quote segments.
Currently enabled:
- split by "On ... wrote:"
"""

import re

try:
    from .helper_sanitize import sanitize_text
except ImportError:  # pragma: no cover
    from helper_sanitize import sanitize_text


# ------------------------------------------------------------
# Quote split strategies (phase 1)
# ------------------------------------------------------------

_ON_WROTE_RE = re.compile(r"^\s*On .{0,200}\bwrote\s*:\s*$")
_HDR_RE = re.compile(r"^\s*(From|Sent|Date|To|Cc|Subject)\s*:\s*", re.I)


def _split_quote_by_on_wrote(text: str) -> list[str]:
    """
    Strategy 1:
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


# Strategy registry (shell)
QUOTE_SPLIT_STRATEGIES = [
    _split_quote_by_on_wrote,
]


def _apply_quote_split_strategies(text: str) -> list[str]:
    """
    Apply quote split strategies sequentially.
    Each strategy may further split segments; no-op if no match.
    """
    segments = [text]
    for strat in QUOTE_SPLIT_STRATEGIES:
        next_segments: list[str] = []
        for seg in segments:
            parts = strat(seg)
            next_segments.extend(parts if parts else [])
        segments = next_segments
    return segments


# ------------------------------------------------------------
# Quote-depth splitter strategies (phase 0)
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
