from __future__ import annotations

import re

from helper.utils_imap_types import EmailMessage  # type: ignore

REVIEW_SUBJECT_MARKER = "[vX]"
REVIEW_SUBJECT_PATTERN = re.compile(r"\[v\d+\]")
REVIEW_SUBJECT_IMAP_QUERY = REVIEW_SUBJECT_MARKER.replace("X]", "")


# Be tolerant to mail-client reformatting:
# - Some clients may keep the "====" separators on the same line as the header/footer.
# - Some clients may trim/alter surrounding whitespace.
_HEADER_RE = re.compile(r"^\s*=*\s*ALI'S RESPONSE - VERSION\s+(\d+)\s*=*\s*$", flags=re.MULTILINE)
_FOOTER_RE = re.compile(r"^\s*=*\s*ALI'S RESPONSE ENDED\s*=*\s*$", flags=re.MULTILINE)


def _search_space_from_last_header(body_text: str) -> str:
    """Return the text starting at the last review header, or full text if none."""
    last_header = None
    for match in _HEADER_RE.finditer(body_text):
        last_header = match
    return body_text[last_header.start() :] if last_header else body_text


def _review_body_for_parsing(review_email: EmailMessage) -> str:
    """Normalize and dequote for consistent marker parsing."""
    return _dequote_email_history(_normalize_newlines(review_email.body_text or ""))

def extract_top_reply(body_text: str) -> str:
    """Extract sender's top reply text, excluding quoted history."""
    if not body_text:
        return ""
    footer = _FOOTER_RE.search(body_text)
    if footer:
        body_text = body_text[: footer.start()]
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


def extract_last_review_draft(review_email: EmailMessage) -> str:
    """
    Extract the most recent draft text from a replied-to `[ALI REVIEW]` email.

    Strategy:
    - Normalize/dequote the body to make markers easier to find.
    - Locate the last "ALI'S RESPONSE - VERSION N" header and return the block until
      "ALI'S RESPONSE ENDED" (or end of message if footer is missing).
    """
    body = _review_body_for_parsing(review_email)
    search_space = _search_space_from_last_header(body)
    header = None
    for match in _HEADER_RE.finditer(search_space):
        header = match
    if not header:
        raise ValueError("Cannot locate review header in review email")

    after_header = search_space[header.end() :].lstrip("\n")
    footer = _FOOTER_RE.search(after_header)
    return (after_header[: footer.start()] if footer else after_header).strip()


def extract_last_version(review_email: EmailMessage) -> int:
    """Return the highest version found in the (dequoted) email history."""
    body = _review_body_for_parsing(review_email)
    versions = [int(m.group(1)) for m in _HEADER_RE.finditer(body)]
    return max(versions) if versions else 1
