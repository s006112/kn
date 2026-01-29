#!/usr/bin/env python3
"""
helper_parse_email_to_raw.py

Responsibility:
Convert a Python email.message.EmailMessage into a list of "raw block" dicts by splitting the message body
using leading ">" quote depth, then sanitizing each resulting segment.

Used by:
* helper/helper_parse_raw_to_jsonl.py

Pipelines:
- email_message -> body_part -> body_text -> quote_depth_split -> sanitize -> raw_blocks

Invariants:
- Only the selected body part content is parsed; headers are ignored.
- Quote depth is derived solely from leading ">" characters.
- Sanitization runs after splitting, per segment.
- Output blocks use 1-based sequential "page" numbering.

Out of scope:
- MIME traversal beyond selecting a preferred body part.
- Heuristics for signatures, reply headers, or client-specific quoting.
- Attachment extraction or decoding beyond email API accessors.
"""

import re
from helper_sanitize import sanitize_text

# ------------------------------------------------------------
# Quote-depth splitter
# ------------------------------------------------------------
# Quote-depth is used because it is a stable structural signal across many clients without needing
# to guess at language-, client-, or header-specific reply separators.
# ------------------------------------------------------------

_QUOTE_DEPTH_RE = re.compile(r"^(>+)\s*(.*)")


def _split_by_quote_depth(text: str):
    """
    Purpose:
    Group lines by leading quote depth and return ordered (depth, segment) pairs.

    Inputs:
    - text: email body text to split.

    Outputs:
    - List[Tuple[int, str]]: one entry per quote depth with non-empty, joined text.
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
    """
    Purpose:
    Convert an EmailMessage into raw block dicts by selecting a preferred body part and splitting by quote depth.

    Inputs:
    - email: EmailMessage-like object supporting get_body(...) and get_content().
    - email_id: identifier stored as "doc_id" in each output block.

    Outputs:
    - List[dict]: raw blocks with keys: doc_id, text, page, source, part.
    """
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
            "doc_id": email_id,  # was f"email_{email_id}",
            "text": sanitize_text(text),
            "page": page,
            "source": "mbox",
            "part": "body" if depth == 0 else "quote",
        })
        page += 1

    return blocks
