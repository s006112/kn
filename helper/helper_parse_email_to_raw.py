#!/usr/bin/env python3
"""
helper_parse_email_to_raw.py
Responsibility:
Convert Email -> RawBlock (email body only)
"""


def parse_email_body_to_raw_block(email, email_id):
    text = email.get_body(preferencelist=("plain", "html"))
    if not text:
        return None
    content = text.get_content().strip()
    if not content:
        return None

    return {
        "doc_id": f"email_{email_id}",  # was f"email_{email_id}"
        "text": content,
        "page": None,
        "source": "mbox",
    }

