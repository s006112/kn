#!/usr/bin/env python3
"""
helper_parse_email_to_raw.py
Responsibility:
Convert Email -> RawBlock list (email body + quoted history)
"""

import re


_QUOTE_SPLIT_PATTERNS = [
    re.compile(r"^From:\s.+$", re.M),
    re.compile(r"^On .+ wrote:\s*$", re.M),
    re.compile(r"^.*於 .+ 寫道:\s*$", re.M),
]


def _split_body_and_quote(text: str):
    for pat in _QUOTE_SPLIT_PATTERNS:
        m = pat.search(text)
        if not m:
            continue

        before = text[:m.start()].strip()
        after = text[m.start():].strip()

        # 最小条件：quote 不能只是一行 header
        after_lines = after.splitlines()
        if len(after_lines) < 2:
            continue

        # 第二行必须有内容
        if not after_lines[1].strip():
            continue

        return before, after

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
