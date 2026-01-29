#!/usr/bin/env python3
"""
helper_parse_email_to_raw.py
Responsibility:
Convert Email -> RawBlock list using quote-depth based splitting.
This is robust, format-agnostic, and does not depend on guessing headers.
"""

import re
from helper_sanitize import sanitize_text

# ------------------------------------------------------------
# Quote-depth splitter
# ------------------------------------------------------------
# Rules:
#   - Lines starting with one or more '>' define quote depth.
#   - depth = number of leading '>' characters.
#   - depth 0 = latest body
#   - depth >=1 = quoted history
#   - Each depth is treated as one message segment.
#   - Sanitization is applied only after splitting.
# ------------------------------------------------------------

_QUOTE_DEPTH_RE = re.compile(r"^(>+)\s*(.*)")


def _split_by_quote_depth(text: str):
    """
    Return ordered list of (depth, text_segment).
    Depth 0 is body, depth >=1 are nested quoted messages.
    """
    buckets = {}

    for line in text.splitlines():
        m = _QUOTE_DEPTH_RE.match(line)
        if m:
            depth = len(m.group(1))
            content = m.group(2)
        else:
            depth = 0
            content = line

        buckets.setdefault(depth, []).append(content)

    segments = []
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

    for depth, text in segments:
        blocks.append({
            "doc_id": f"email_{email_id}",
            "text": sanitize_text(text),
            "page": page,
            "source": "mbox",
            "part": "body" if depth == 0 else "quote",
        })
        page += 1

    return blocks
