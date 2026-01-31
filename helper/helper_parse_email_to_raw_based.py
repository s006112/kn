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
- Phase 3: split by forwarded markers
- Phase 4: split by Chinese header blocks (发件人/发送时间/收件人/抄送/主题)
"""

import re
from helper_sanitize import sanitize_text
from html import unescape

# ------------------------------------------------------------
# Phase 1: on_wrote
# ------------------------------------------------------------

_ON_WROTE_LINE_RE = re.compile(r"^\s*On .{0,200}\bwrote\s*:\s*$", re.I)
_CN_WROTE_LINE_RE = re.compile(r"^\s*.{1,200}\s+於\s+.{1,50}\s+寫道\s*:\s*$")
_HDR_RE = re.compile(r"^\s*(From|Sent|Date|To|Cc|Subject)\s*:\s*", re.I)

def _split_quote_by_on_wrote(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []

    lines = t.splitlines()
    if len(lines) < 3:
        return [t]

    cuts = [0]
    last_cut = 0

    for i in range(1, len(lines) - 1):
        if i - last_cut < 2:
            continue
        line = lines[i]
        if not (_ON_WROTE_LINE_RE.match(line) or _CN_WROTE_LINE_RE.match(line)):
            continue

        next1 = lines[i + 1].strip()
        next2 = lines[i + 2].strip() if i + 2 < len(lines) else ""
        if next1 == "" or _HDR_RE.match(next1) or _HDR_RE.match(next2):
            cuts.append(i)
            last_cut = i

    if len(cuts) == 1:
        return [t]

    cuts.append(len(lines))
    return [
        "\n".join(lines[a:b]).strip()
        for a, b in zip(cuts, cuts[1:])
        if "\n".join(lines[a:b]).strip()
    ]


# ------------------------------------------------------------
# Phase 2: RFC-like header block (English)
# ------------------------------------------------------------

_HDR_KEY_RE = re.compile(r"^\s*(From|Date|Sent|To|Subject|Cc)\s*:\s*", re.I)

def _split_quote_by_header_block(text: str, *, min_keys: int = 3, lookahead: int = 6) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []

    lines = t.splitlines()
    if len(lines) < lookahead:
        return [t]

    cuts = [0]
    last_cut = 0

    for i in range(1, len(lines)):
        if i - last_cut < lookahead:
            continue
        keys = {
            _HDR_KEY_RE.match(lines[j]).group(1).lower()
            for j in range(i, min(i + lookahead, len(lines)))
            if _HDR_KEY_RE.match(lines[j])
        }
        if len(keys) >= min_keys:
            cuts.append(i)
            last_cut = i

    if len(cuts) == 1:
        return [t]

    cuts.append(len(lines))
    return [
        "\n".join(lines[a:b]).strip()
        for a, b in zip(cuts, cuts[1:])
        if "\n".join(lines[a:b]).strip()
    ]


# ------------------------------------------------------------
# Phase 3: forwarded markers
# ------------------------------------------------------------

_FWD_CN_LINE_RE = re.compile(r"^\s*(?:-+\s*)?(?:轉寄郵件|转寄邮件)\s*(?:-+)?\s*$")
_FWD_BEGIN_LINE_RE = re.compile(r"^\s*Begin forwarded message\s*:?\s*$", re.I)
_FWD_SIMPLE_LINE_RE = re.compile(r"^\s*Forwarded message\s*:?\s*$", re.I)

def _is_forwarded_marker_line(line: str) -> bool:
    return (
        _FWD_CN_LINE_RE.match(line)
        or _FWD_BEGIN_LINE_RE.match(line)
        or _FWD_SIMPLE_LINE_RE.match(line)
    )

def _split_quote_by_forward_email(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []

    lines = t.splitlines()
    if len(lines) < 2:
        return [t]

    cuts = [0]
    last_cut = 0

    for i in range(1, len(lines)):
        if i - last_cut < 2:
            continue
        if _is_forwarded_marker_line(lines[i]):
            cuts.append(i)
            last_cut = i

    if len(cuts) == 1:
        return [t]

    cuts.append(len(lines))
    return [
        "\n".join(lines[a:b]).strip()
        for a, b in zip(cuts, cuts[1:])
        if "\n".join(lines[a:b]).strip()
    ]


# ------------------------------------------------------------
# Phase 4: Chinese header block (NEW)
# ------------------------------------------------------------

_CN_HDR_KEY_RE = re.compile(
    r"^\s*(发件人|寄件人|发送时间|发送日期|收件人|抄送|副本|主题)\s*[:：]\s*"
)

def _split_quote_by_cn_header_block(
    text: str, *, min_keys: int = 3, lookahead: int = 8
) -> list[str]:
    """
    Detect Chinese email header blocks such as:
      发件人：
      发送时间：
      收件人：
      抄送：
      主题：

    Rule identical to Phase 2, but with Chinese keys.
    """
    t = (text or "").strip()
    if not t:
        return []

    lines = t.splitlines()
    if len(lines) < lookahead:
        return [t]

    cuts = [0]
    last_cut = 0

    for i in range(1, len(lines)):
        if i - last_cut < lookahead:
            continue
        keys = {
            _CN_HDR_KEY_RE.match(lines[j]).group(1)
            for j in range(i, min(i + lookahead, len(lines)))
            if _CN_HDR_KEY_RE.match(lines[j])
        }
        if len(keys) >= min_keys:
            cuts.append(i)
            last_cut = i

    if len(cuts) == 1:
        return [t]

    cuts.append(len(lines))
    return [
        "\n".join(lines[a:b]).strip()
        for a, b in zip(cuts, cuts[1:])
        if "\n".join(lines[a:b]).strip()
    ]


# ------------------------------------------------------------
# Strategy registry (ORDER MATTERS)
# ------------------------------------------------------------

QUOTE_SPLIT_STRATEGIES = [
    _split_quote_by_on_wrote,
    _split_quote_by_header_block,
    _split_quote_by_forward_email,
    _split_quote_by_cn_header_block,   # Phase 4 appended, no interference
]

def _apply_quote_split_strategies(text: str) -> list[str]:
    segments = [text]
    for strat in QUOTE_SPLIT_STRATEGIES:
        next_segments = []
        for seg in segments:
            parts = strat(seg)
            next_segments.extend(parts if parts else [])
        segments = next_segments
    return segments


# ------------------------------------------------------------
# Phase 0: quote-depth splitter (plain + HTML)
# ------------------------------------------------------------

_QUOTE_DEPTH_RE = re.compile(r"^(>+)\s*(.*)", re.M)

def _split_by_quote_depth(text: str) -> list[tuple[int, str]]:
    """
    Phase 0 quote-depth splitter (minimum drop-in)

    - If '>' quote markers exist: keep ORIGINAL behavior 100% unchanged.
    - Else (Foxmail/plain flattened threads): detect repeated header-blocks
      (CN/EN) and map each block start to deeper quote depth.
    """
    if not text:
        return []

    lines = text.splitlines()

    # ------------------------------------------------------------------
    # Fast path: ORIGINAL behavior when RFC-style '>' quoting exists
    # ------------------------------------------------------------------
    has_gt_quote = any(l.lstrip().startswith(">") for l in lines)
    if has_gt_quote:
        segments = []
        current_depth = None
        buf = []

        for line in lines:
            m = _QUOTE_DEPTH_RE.match(line)
            depth, content = (len(m.group(1)), m.group(2)) if m else (0, line)

            if current_depth is None:
                current_depth = depth
                buf.append(content)
            elif depth == current_depth:
                buf.append(content)
            else:
                seg = "\n".join(buf).strip()
                if seg:
                    segments.append((current_depth, seg))
                current_depth = depth
                buf = [content]

        if buf:
            seg = "\n".join(buf).strip()
            if seg:
                segments.append((current_depth, seg))

        return segments

    # ------------------------------------------------------------------
    # Fallback: header-block depth mapping for plain-text threads
    # ------------------------------------------------------------------
    lookahead = 10
    min_keys = 3
    cut_points = [0]
    last_cut = 0
    block_starts = []

    n = len(lines)
    for i in range(1, n):
        # spacing guard
        if i - last_cut < lookahead:
            continue

        keys = set()
        matches = []  # record real header line positions

        for j in range(i, min(i + lookahead, n)):
            m1 = _HDR_KEY_RE.match(lines[j])
            if m1:
                keys.add(m1.group(1).lower())
                matches.append(j)
                continue
            m2 = _CN_HDR_KEY_RE.match(lines[j])
            if m2:
                keys.add(m2.group(1))
                matches.append(j)
                continue

        if len(keys) >= min_keys and matches:
            first_header_j = matches[0]

            # spacing guard should be evaluated on real header start
            if first_header_j - last_cut < lookahead:
                continue

            block_starts.append(first_header_j)
            cut_points.append(first_header_j)
            last_cut = first_header_j

    # Not enough evidence => treat as single body
    if len(block_starts) < 1:
        return [(0, text.strip())] if text.strip() else []

    cut_points.append(n)

    # Build segments: first = depth 0, subsequent blocks = depth 1..k
    out: list[tuple[int, str]] = []
    for idx, (a, b) in enumerate(zip(cut_points, cut_points[1:])):
        seg = "\n".join(lines[a:b]).strip()
        if not seg:
            continue
        depth = 0 if idx == 0 else idx
        out.append((depth, seg))

    return out

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
        else:
            for sub in _apply_quote_split_strategies(text):
                _emit("quote", sub)

    return blocks
