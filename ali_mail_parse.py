from __future__ import annotations

import re

from helper.utils_imap_types import EmailMessage  # type: ignore

from ali_review_proto import INTERNAL_REVIEW_ANCHOR, ORIGINAL_MESSAGE_MARKER

def extract_top_reply(body_text: str) -> str:
    """Extract sender's top reply text, excluding quoted history."""
    if not body_text:
        return ""
    if ORIGINAL_MESSAGE_MARKER in body_text:
        body_text = body_text.split(ORIGINAL_MESSAGE_MARKER, 1)[0]
    return body_text.strip()


def _normalize_newlines(text: str) -> str:
    """Normalize CRLF/CR newlines to `\\n` for consistent parsing."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _dequote_email_history(body_text: str) -> str:
    """
    Many email clients quote history with leading ">" (or ">>", etc.).
    Dequote for parsing so we can reliably find our own markers.
    """
    dequoted_lines: list[str] = []
    for line in body_text.splitlines():
        while True:
            match = re.match(r"^\s*>+\s?(.*)$", line)
            if not match:
                break
            line = match.group(1)
        dequoted_lines.append(line)
    return "\n".join(dequoted_lines)


def _search_space_from_last_anchor(body_text: str, anchor: str) -> str:
    """Search from the last occurrence of `anchor` to avoid matching older drafts in long threads."""
    start_search_at = body_text.rfind(anchor)
    return body_text[start_search_at:] if start_search_at != -1 else body_text


def extract_last_review_draft(review_email: EmailMessage) -> str:
    """
    Extract the most recent draft text from a replied-to `[ALI REVIEW]` email.

    Strategy:
    - Normalize/dequote the body to make markers easier to find.
    - Narrow to the text after the last `INTERNAL_REVIEW_ANCHOR` to avoid older versions.
    - Locate the last `EDIT VERSION: vX` header; return the draft block beneath it.
    """
    body = _dequote_email_history(_normalize_newlines(review_email.body_text or ""))
    search_space = _search_space_from_last_anchor(body, INTERNAL_REVIEW_ANCHOR)

    header_match = None
    for match in re.finditer(r"^EDIT VERSION:\s*v(\d+)\s*$", search_space, flags=re.MULTILINE):
        header_match = match

    if header_match is None:
        raise ValueError("Cannot locate EDIT VERSION header in review email")

    after_header = search_space[header_match.end() :]
    after_header = after_header.lstrip("\n")

    sep_pattern = re.compile(r"^[=]{10,}\s*$")
    kept: list[str] = []
    for line in after_header.splitlines():
        if line.strip() == "SENDER ACTION REQUIRED":
            break
        if sep_pattern.match(line.strip()):
            break
        kept.append(line)

    return "\n".join(kept).strip()


def extract_last_version(review_email: EmailMessage) -> int:
    """Return the highest `EDIT VERSION: vX` found in the (dequoted) email history."""
    body = _dequote_email_history(_normalize_newlines(review_email.body_text or ""))
    search_space = _search_space_from_last_anchor(body, INTERNAL_REVIEW_ANCHOR)

    versions = [int(m.group(1)) for m in re.finditer(r"EDIT VERSION:\s*v(\d+)", search_space)]
    return max(versions) if versions else 1

