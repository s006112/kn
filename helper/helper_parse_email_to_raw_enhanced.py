#!/usr/bin/env python3
"""
helper_parse_email_to_raw_clean.py

Goal:
Turn messy real-world email bodies into CLEAN, STABLE, NON-FRAGMENTED raw blocks,
ready for canonical JSONL.

Pipeline (strict order):
0) extract text (plain/html) + basic cleanup
1) repair quoting markers (insert missing '>' for header-style historical blocks)
2) split by quote depth (order-preserving run splitter)
3) for each quote segment (depth>=1): split into message-like chunks using ONE boundary detector
4) merge micro-fragments (header-only or too-short) to avoid over-splitting
5) emit RawBlocks (page increments)

Invariants:
- Never reorder content.
- Never delete semantic content (only trim whitespace + decode artifacts).
- Boundary detection is single-pass (no cascading multi-strategy splitting).
"""

import re
import quopri
from html import unescape

# If you already have helper_sanitize.sanitize_text, keep using it.
try:
    from helper_sanitize import sanitize_text
except Exception:
    def sanitize_text(s: str) -> str:
        return (s or "").strip()


# ============================================================
# 0) Text cleanup (safe, minimal)
# ============================================================

_TAG_RE = re.compile(r"<[^>]+>")

def _html_to_text(s: str) -> str:
    # minimal; avoid fancy parsing to keep deterministic
    s = unescape(s or "")
    s = s.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = s.replace("</p>", "\n").replace("</div>", "\n")
    s = _TAG_RE.sub("", s)
    return s

_QP_HEX_RE = re.compile(r"(?:=[0-9A-Fa-f]{2}){3,}")  # heuristic: lots of =XX

def _maybe_qp_decode(s: str) -> str:
    t = s or ""
    # If it looks like quoted-printable artifacts survived, try decode
    if "=\n" in t or "=20" in t or _QP_HEX_RE.search(t):
        try:
            b = t.encode("utf-8", "ignore")
            t2 = quopri.decodestring(b).decode("utf-8", "replace")
            return t2
        except Exception:
            return t
    return t

def _normalize_newlines(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


# ============================================================
# 1) Quoting repair: insert missing '>' for historical blocks
#    (STRUCTURE repair only; monotonic + idempotent)
# ============================================================

_EN_WROTE_RE = re.compile(r"^\s*On .{0,200}\bwrote\s*:\s*$", re.I)
_CN_WROTE_RE = re.compile(r"^\s*.{1,200}\s+於\s+.{1,80}\s+寫道\s*:\s*$")

# single-line forwarded markers
_FWD_BEGIN_RE = re.compile(r"^\s*Begin forwarded message\s*:?\s*$", re.I)
_FWD_SIMPLE_RE = re.compile(r"^\s*Forwarded message\s*:?\s*$", re.I)
_FWD_BAR_RE = re.compile(r"^\s*-+\s*(?:轉寄郵件|转寄邮件)\s*-+\s*$")

# header keys (EN + CN) - used as "quote block start"
_HDR_LINE_RE = re.compile(
    r"^\s*(From|Sent|Date|To|Cc|Subject)\s*:\s*",
    re.I,
)
_CN_HDR_LINE_RE = re.compile(
    r"^\s*(发件人|寄件人|发送时间|发送日期|收件人|抄送|副本|主题)\s*[:：]\s*"
)

def _is_history_block_start(line: str) -> bool:
    if not line:
        return False
    l = line.strip()
    return bool(
        _EN_WROTE_RE.match(l)
        or _CN_WROTE_RE.match(l)
        or _FWD_BEGIN_RE.match(l)
        or _FWD_SIMPLE_RE.match(l)
        or _FWD_BAR_RE.match(l)
        or _HDR_LINE_RE.match(l)
        or _CN_HDR_LINE_RE.match(l)
    )

def insert_missing_quote_markers(text: str) -> str:
    """
    Convert header-style historical blocks into RFC-like quoted lines by prepending '> '.

    Key rule to avoid over-quoting:
    - We only enter quote_mode when we see a strong start marker.
    - quote_mode ends on a blank line *after* we've entered it.
    - If a line is already quoted (starts with '>'), we keep it and DO NOT force quote_mode forward.
    """
    t = _normalize_newlines(text or "")
    if not t.strip():
        return ""

    lines = t.split("\n")
    out = []
    quote_mode = False

    for line in lines:
        if line.lstrip().startswith(">"):
            out.append(line)
            quote_mode = False
            continue

        if _is_history_block_start(line):
            quote_mode = True
            out.append("> " + line)
            continue

        if quote_mode:
            if not line.strip():
                quote_mode = False
                out.append(line)
            else:
                out.append("> " + line)
        else:
            out.append(line)

    return "\n".join(out)


# ============================================================
# 2) Quote-depth run splitter (order-preserving)
# ============================================================

_QUOTE_DEPTH_RE = re.compile(r"^(>+)\s*(.*)$")

def split_by_quote_depth_runs(text: str) -> list[tuple[int, str]]:
    t = _normalize_newlines(text or "")
    if not t.strip():
        return []

    segments: list[tuple[int, str]] = []
    current_depth = None
    buf: list[str] = []

    for line in t.split("\n"):
        m = _QUOTE_DEPTH_RE.match(line)
        if m:
            depth = len(m.group(1))
            content = m.group(2)
        else:
            depth = 0
            content = line

        if current_depth is None:
            current_depth = depth
            buf.append(content)
            continue

        if depth == current_depth:
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


# ============================================================
# 3) ONE boundary detector for quote segment: "message-like chunking"
#    Core: detect header BLOCKS (cluster), not individual header lines.
# ============================================================

# detect an EN header block cluster within window
_HDR_KEY_RE = re.compile(r"^\s*(From|Sent|Date|To|Cc|Subject)\s*:\s*", re.I)
_CN_HDR_KEY_RE = re.compile(r"^\s*(发件人|寄件人|发送时间|发送日期|收件人|抄送|副本|主题)\s*[:：]\s*")

def _find_header_block_start(lines: list[str], i: int, lookahead: int, min_keys: int) -> int | None:
    """
    If within [i, i+lookahead) we observe >=min_keys distinct header keys,
    return the FIRST matched header line index as the block start.
    """
    keys = set()
    first = None
    for j in range(i, min(i + lookahead, len(lines))):
        m = _HDR_KEY_RE.match(lines[j])
        if m:
            keys.add(m.group(1).lower())
            if first is None:
                first = j
            continue
        m2 = _CN_HDR_KEY_RE.match(lines[j])
        if m2:
            keys.add(m2.group(1))
            if first is None:
                first = j
            continue
    if first is not None and len(keys) >= min_keys:
        return first
    return None

def split_quote_into_messages(text: str) -> list[str]:
    """
    Split one quote-run into message-like chunks.

    Boundaries considered (single pass):
    - 'On ... wrote:' / '... 於 ... 寫道:'
    - forwarded bar markers
    - header BLOCK clusters (>=3 keys in a window)

    IMPORTANT:
    - We cut at the start of the detected boundary.
    - We do NOT repeatedly re-split the produced chunks.
    """
    t = _normalize_newlines(text or "").strip()
    if not t:
        return []

    lines = t.split("\n")
    n = len(lines)

    # parameters tuned to avoid micro-fragmentation
    lookahead = 10
    min_keys = 3
    min_gap = 6  # minimum lines between boundaries (anti-noise)

    cut_points = [0]
    last_cut = 0

    for i in range(1, n):
        if i - last_cut < min_gap:
            continue

        line = lines[i].strip()

        # strong single-line boundaries
        if _EN_WROTE_RE.match(line) or _CN_WROTE_RE.match(line) or _FWD_BAR_RE.match(line) or _FWD_BEGIN_RE.match(line) or _FWD_SIMPLE_RE.match(line):
            cut_points.append(i)
            last_cut = i
            continue

        # header BLOCK boundary (cluster)
        start = _find_header_block_start(lines, i, lookahead=lookahead, min_keys=min_keys)
        if start is not None and start - last_cut >= min_gap:
            cut_points.append(start)
            last_cut = start
            continue

    cut_points = sorted(set(cut_points))
    if len(cut_points) == 1:
        return [t]

    cut_points.append(n)

    out = []
    for a, b in zip(cut_points, cut_points[1:]):
        seg = "\n".join(lines[a:b]).strip()
        if seg:
            out.append(seg)
    return out


# ============================================================
# 4) Anti-fragment merger (must-have)
# ============================================================

def _is_header_only(seg: str) -> bool:
    lines = [ln.strip() for ln in (seg or "").split("\n") if ln.strip()]
    if not lines:
        return True
    # if almost all lines are header-like, treat as fragment
    hdr_like = 0
    for ln in lines:
        if _HDR_KEY_RE.match(ln) or _CN_HDR_KEY_RE.match(ln):
            hdr_like += 1
        elif ln.lower().startswith("subject:"):
            hdr_like += 1
        elif ln.startswith("----") or _FWD_BAR_RE.match(ln) or _FWD_BEGIN_RE.match(ln) or _FWD_SIMPLE_RE.match(ln):
            hdr_like += 1
    return hdr_like >= max(2, int(len(lines) * 0.7))

def merge_micro_fragments(chunks: list[str], *, min_chars: int = 200) -> list[str]:
    """
    Merge tiny / header-only chunks forward to avoid page32~35 disaster.

    Rule:
    - If chunk is header-only OR <min_chars -> merge into next (or previous if last)
    """
    if not chunks:
        return []
    chunks = [c for c in chunks if (c or "").strip()]
    if not chunks:
        return []

    out: list[str] = []
    i = 0
    while i < len(chunks):
        cur = chunks[i].strip()
        if i < len(chunks) - 1 and (_is_header_only(cur) or len(cur) < min_chars):
            nxt = chunks[i + 1].strip()
            merged = cur + "\n\n" + nxt
            chunks[i + 1] = merged
            i += 1
            continue
        out.append(cur)
        i += 1

    # if last one still tiny and we have previous, merge backward
    if len(out) >= 2 and (_is_header_only(out[-1]) or len(out[-1]) < min_chars):
        out[-2] = out[-2] + "\n\n" + out[-1]
        out.pop()

    return out


# ============================================================
# 5) Main API (drop-in)
# ============================================================

def parse_email_to_raw_blocks(email, email_id: str):
    """
    Returns list[RawBlock]:
    {
        "doc_id": email_id,
        "text": <clean text>,
        "page": <int>,
        "source": "mbox",
        "part": "body" | "quote",
    }
    """
    text_part = email.get_body(preferencelist=("plain", "html"))
    if not text_part:
        return []

    content = text_part.get_content() or ""
    if not content.strip():
        return []

    # html handling
    ctype = (text_part.get_content_type() or "").lower()
    if ctype == "text/html":
        content = _html_to_text(content)

    content = _maybe_qp_decode(content)
    content = _normalize_newlines(content)

    # STRUCTURE repair first
    content = insert_missing_quote_markers(content)

    # quote-depth runs
    runs = split_by_quote_depth_runs(content)

    blocks = []
    page = 1

    def _emit(part: str, txt: str):
        nonlocal page
        t = sanitize_text(txt)
        if not t:
            return
        blocks.append({
            "doc_id": email_id,
            "text": t,
            "page": page,
            "source": "mbox",
            "part": part,
        })
        page += 1

    for depth, txt in runs:
        if depth == 0:
            _emit("body", txt)
            continue

        # quote: semantic message chunking (single pass) + merge
        chunks = split_quote_into_messages(txt)
        chunks = merge_micro_fragments(chunks, min_chars=200)

        for ch in chunks:
            _emit("quote", ch)

    return blocks
