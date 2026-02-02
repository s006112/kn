#!/usr/bin/env python3
"""
parse_email_to_raw.py

Used by: 
- rag/parse_raw_to_jsonl.py

Responsibility:
Convert Email -> RawBlock list using quote-depth based splitting.
This is robust, format-agnostic, and does not depend on guessing headers.
"""

import re
from helper_sanitize import sanitize_text
from test_save_email_raw_text import save_raw_email_text


# ------------------------------------------------------------
# Phase -1: quote normalization (decoupled helper)
# Responsibility:
#   Insert missing quote markers after forwarded separators.
# ------------------------------------------------------------

_QUOTE_PREFIX_RE = re.compile(r"^(>+)\s*(.*)")  # match quote prefix
_HDR_KEY_RE = re.compile(
    r"\b("
    r"From|Date|Sent|To|Subject|Cc|"
    r"发件人|發件人|发送时间|發送時間|收件人|收件者|抄送|主题|主題|主旨"
    r")\s*:\s*",
    re.I,
)
_FWD_LINE_RE = re.compile(
    r"""^\s*(?:
        # ----------------------------
        # forwarded separators
        # ----------------------------
        -+\s*(?:轉寄郵件|转寄邮件)\s*-+ |      # Chinese
        -+\s*Forwarded\s+Message\s*-+    |      # Outlook/Gmail
        Begin\s+forwarded\s+message:?    |      # Apple Mail

        # ----------------------------
        # reply header lines (NEW)
        # ----------------------------
        .+?\s+於\s+.+?\s+寫道:            |      # Chinese reply header
        On\s+.+?\s+wrote:                       # English reply header
    )\s*$""",
    re.I | re.X,
)

def _is_header_block_boundary(
    lines: list[str],
    start: int,
    *,
    min_keys: int = 3,
    lookahead: int = 6,
) -> bool:

    line = lines[start]

    # ① anchor：当前行必须含 header key
    if not _HDR_KEY_RE.search(line):
        return False

    # ② window 统计
    keys = set()
    for j in range(start, min(start + lookahead, len(lines))):
        for m in _HDR_KEY_RE.finditer(lines[j]):
            keys.add(m.group(1).lower())

    return len(keys) >= min_keys

def insert_quote_markers(text: str) -> str:
    if not text:
        return text

    lines = text.splitlines()
    out = []
    extra_depth = 0

    for line in lines:
        m = _QUOTE_PREFIX_RE.match(line)

        if m:
            depth = len(m.group(1))
            content = m.group(2)
        else:
            depth = 0
            content = line

        # ⭐ pre-increment
        if _FWD_LINE_RE.match(content.strip()):
            extra_depth += 1

        new_depth = depth + extra_depth
        out.append((">" * new_depth + " " + content) if new_depth > 0 else content)

    return "\n".join(out)



def insert_header_block_markers(text: str) -> str:
    if not text:
        return text

    lines = text.splitlines()
    out = []

    i = 0
    extra_depth = 0
    prev_is_header = False

    while i < len(lines):
        line = lines[i]

        m = _QUOTE_PREFIX_RE.match(line)
        if m:
            depth = len(m.group(1))
            content = m.group(2)
        else:
            depth = 0
            content = line

        is_header = _is_header_block_boundary(lines, i)

        # ⭐ pre-increment（关键修复）
        if is_header and not prev_is_header:
            extra_depth += 1

        new_depth = depth + extra_depth
        out.append((">" * new_depth + " " + content) if new_depth > 0 else content)

        prev_is_header = is_header
        i += 1

    return "\n".join(out)


def _needs_html_blockquote_normalization(text: str) -> bool:
    if not text:
        return False

    lower = text.lower()

    # already RFC quoted → skip
    if re.search(r'^\s*>+', text, re.M):
        return False

    if "<blockquote" not in lower:
        return False

    tag_cnt = lower.count("<div") + lower.count("<p") + lower.count("<br")
    if tag_cnt < 8:
        return False

    return True



import re
import html


_BR_RE = re.compile(r"<br\s*/?>", re.I)
_BLOCK_OPEN_RE  = re.compile(r"<blockquote\b[^>]*>", re.I)
_BLOCK_CLOSE_RE = re.compile(r"</blockquote>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def insert_html_blockquote_markers(text: str) -> str:
    """
    HTML → RFC quote adapter

    Convert:
        <blockquote> nesting
    into:
        > depth markers

    Deterministic, no line-loss, no content-loss.
    """

    if not text:
        return text

    # --------------------------------
    # 1. normalize common html breaks
    # --------------------------------
    text = _BR_RE.sub("\n", text)
    text = re.sub(r"</?(div|p)\b[^>]*>", "\n", text, flags=re.I)

    # decode &nbsp; etc
    text = html.unescape(text)

    # --------------------------------
    # 2. walk token by token (NOT line by line)
    # --------------------------------
    tokens = re.split(r"(<[^>]+>)", text)

    depth = 0
    buf = []
    out_lines = []

    def flush():
        nonlocal buf
        if not buf:
            return
        line = "".join(buf).strip()
        if line:
            if depth > 0:
                out_lines.append(">" * depth + " " + line)
            else:
                out_lines.append(line)
        buf = []

    for tok in tokens:

        if not tok:
            continue

        # open blockquote, close blockquote
        if _BLOCK_OPEN_RE.fullmatch(tok) or _BLOCK_CLOSE_RE.fullmatch(tok):
            flush()
            continue

        if tok.startswith("<"):
            continue

        # plain text
        parts = tok.splitlines(True)
        for p in parts:
            if p.endswith("\n"):
                buf.append(p.rstrip("\n"))
                flush()
            else:
                buf.append(p)

    flush()

    return "\n".join(out_lines)


# ------------------------------------------------------------
# Quote-depth splitter (phase 0)
# ------------------------------------------------------------

def _split_by_quote_depth(text: str) -> list[tuple[int, str]]:
    """
    Phase 0 (order-preserving run splitter)

    - Scan lines in original order
    - Compute quote depth per line
    - Split segments whenever depth changes
    - Do NOT merge non-consecutive runs
    - Do NOT reorder by depth
    """
    segments: list[tuple[int, str]] = []

    current_depth = None
    buf: list[str] = []

    for line in text.splitlines():
        m = _QUOTE_PREFIX_RE.match(line)
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

    if _needs_html_blockquote_normalization(content):
        content = insert_html_blockquote_markers(content)    
    content = insert_quote_markers(content)
    content = insert_header_block_markers(content)


    #save_raw_email_text(email_id=f"{email_id}_q2", content=content,)

    if not content:
        return []

    segments = _split_by_quote_depth(content)   # Quote-depth splitter (phase 0)

    blocks = []
    page = 1

    for depth, text in segments:
        blocks.append({
            "doc_id": email_id,  # was f"email_{email_id}",
            "text": sanitize_text(text),
            "page": page,
            "source": "mbox",
            "part": "body" if depth == 0 else "quote",
        })
        page += 1

    return blocks
