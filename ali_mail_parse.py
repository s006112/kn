from __future__ import annotations

from ali_review_proto import ORIGINAL_MESSAGE_MARKER


def extract_top_reply(body_text: str) -> str:
    """Extract sender's top reply text, excluding quoted history."""
    if not body_text:
        return ""
    if ORIGINAL_MESSAGE_MARKER in body_text:
        body_text = body_text.split(ORIGINAL_MESSAGE_MARKER, 1)[0]
    return body_text.strip()
