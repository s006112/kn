#!/usr/bin/env python3
"""
helper_parse_email_to_raw_based.py
Responsibility:
Convert Email -> RawBlock list using quote-depth based splitting.
This is robust, format-agnostic, and does not depend on guessing headers.
"""

import re
from helper_sanitize import sanitize_text
from helper_save_email_raw_text import save_raw_email_text


# ------------------------------------------------------------
# Phase -1: quote normalization (decoupled helper)
# Responsibility:
#   Insert missing quote markers after forwarded separators.
#
# Rules:
#   - detect "-------- 轉寄郵件 --------" / "转寄邮件"
#   - when separator depth == current depth:
#         all subsequent lines depth +1
#   - supports nesting (multiple separators accumulate)
#   - pure text rewrite, no split logic here
# ------------------------------------------------------------

_FWD_LINE_RE = re.compile(
    r"""^\s*(?:
        -+\s*(?:轉寄郵件|转寄邮件)\s*-+ |   # Chinese
        -+\s*Forwarded\s+Message\s*-+   |   # Outlook/Gmail style
        Begin\s+forwarded\s+message:?       # Apple Mail
    )\s*$""",
    re.I | re.X,
)


_QUOTE_PREFIX_RE = re.compile(r"^(>+)\s*(.*)")


def insert_quote_markers(text: str) -> str:
    if not text:
        return text

    lines = text.splitlines()
    out = []

    extra_depth = 0  # accumulated +N

    for line in lines:
        m = _QUOTE_PREFIX_RE.match(line)

        if m:
            depth = len(m.group(1))
            content = m.group(2)
        else:
            depth = 0
            content = line

        new_depth = depth + extra_depth
        new_line = (">" * new_depth + " " + content) if new_depth > 0 else content
        out.append(new_line)

        # if this line is forwarded separator → increase depth for next lines
        if _FWD_LINE_RE.match(content.strip()):
            extra_depth += 1

    return "\n".join(out)


# ------------------------------------------------------------
# Quote-depth splitter (phase 0)
# ------------------------------------------------------------

_QUOTE_DEPTH_RE = re.compile(r"^(>+)\s*(.*)")


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
