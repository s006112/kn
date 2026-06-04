#!/usr/bin/env python3
"""
parse_email_to_raw_based.py
Responsibility:
Convert Email -> RawBlock list using quote-depth based splitting.
This is robust, format-agnostic, and does not depend on guessing headers.
"""

import re
from helper_sanitize import sanitize_text
from tool.test_save_email_raw_text import save_raw_email_text


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

        new_depth = depth + extra_depth
        out.append((">" * new_depth + " " + content) if new_depth > 0 else content)

        if _FWD_LINE_RE.match(content.strip()):
            extra_depth += 1

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

        new_depth = depth + extra_depth
        out.append((">" * new_depth + " " + content) if new_depth > 0 else content)

        is_header = _is_header_block_boundary(lines, i)

        # trigger only on the first line of a detected header block
        # NOTE: do NOT require depth==0, otherwise quoted header blocks never trigger
        if is_header and not prev_is_header:
            extra_depth += 1

        prev_is_header = is_header
        i += 1

    return "\n".join(out)

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

    # Phase -1 normalize
    content = insert_quote_markers(content)

    # save based normalization
    save_raw_email_text(
        email_id=f"{email_id}_based",
        content=content,
    )

    content = insert_header_block_markers(content)
    save_raw_email_text(email_id=f"{email_id}_q1", content=content,)

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
