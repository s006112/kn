#!/usr/bin/env python3
"""
helper_parse_email_to_raw.py
Responsibility:
Convert Email -> RawBlock list (email body + quoted history)
"""

import re


_QUOTE_SPLIT_PATTERNS = [
    re.compile(r"^>+", re.M),
    re.compile(r"^-----Original Message-----", re.M),
    re.compile(r"^From: .*", re.M),
    re.compile(r"^.*於 .* 寫道:", re.M),
    re.compile(r"^.* wrote:", re.M),
]


def _split_body_and_quote(text: str):
    for pat in _QUOTE_SPLIT_PATTERNS:
        m = pat.search(text)
        if m:
            return text[:m.start()].strip(), text[m.start():].strip()
    return text.strip(), ""


def parse_email_to_raw_blocks(email, email_id):
    text_part = email.get_body(preferencelist=("plain", "html"))
    if not text_part:
        return []

    content = text_part.get_content().strip()
    if not content:
        return []

    body, quote = _split_body_and_quote(content)

    blocks = []

    if body:
        blocks.append({
            "doc_id": f"email_{email_id}",
            "text": body,
            "page": None,
            "source": "mbox",
            "part": "body",
        })

    if quote:
        blocks.append({
            "doc_id": f"email_{email_id}",
            "text": quote,
            "page": None,
            "source": "mbox",
            "part": "quote",
        })

    return blocks
