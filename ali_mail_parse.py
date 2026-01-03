from __future__ import annotations

import re

from helper.utils_imap_types import EmailMessage  # type: ignore

REVIEW_SUBJECT_MARKER = "[ALI:vX]"
REVIEW_SUBJECT_PATTERN = re.compile(r"\[ALI:v\d+\]", flags=re.IGNORECASE)
REVIEW_SUBJECT_IMAP_QUERY = REVIEW_SUBJECT_MARKER.replace("X]", "")


# Be tolerant to mail-client reformatting:
# - Some clients may keep the "====" separators on the same line as the header/footer.
# - Some clients may trim/alter surrounding whitespace.
_HEADER_RE = re.compile(r"^\s*=*\s*ALI'S RESPONSE - VERSION\s+(\d+)\s*=*\s*$", flags=re.MULTILINE)
_FOOTER_RE = re.compile(r"^\s*=*\s*ALI'S RESPONSE ENDED\s*=*\s*$", flags=re.MULTILINE)
_QUOTE_PREFIX_RE = re.compile(r"^\s*>+\s?(.*)$")


def _search_space_from_last_header(body_text: str) -> str:
    """Return the text starting at the last review header, or full text if none."""
    last_header = None
    for match in _HEADER_RE.finditer(body_text):
        last_header = match
    return body_text[last_header.start() :] if last_header else body_text


def _review_body_for_parsing(review_email: EmailMessage) -> str:
    """Normalize and dequote for consistent marker parsing."""
    body_text = (review_email.body_text or "").replace("\r\n", "\n").replace("\r", "\n")
    dequoted_lines: list[str] = []
    for line in body_text.splitlines():
        while True:
            match = _QUOTE_PREFIX_RE.match(line)
            if not match:
                break
            line = match.group(1)
        dequoted_lines.append(line)
    return "\n".join(dequoted_lines)

def extract_last_version(review_email: EmailMessage) -> int:
    """Return the highest version found in the (dequoted) email history."""
    body = _review_body_for_parsing(review_email)
    versions = [int(m.group(1)) for m in _HEADER_RE.finditer(body)]
    return max(versions) if versions else 1

def extract_top_reply(body_text: str) -> str:
    """
    Extract sender's top reply text, excluding quoted history.

    >>> extract_top_reply("> old line\\n> another old line")
    ''
    >>> extract_top_reply("Please make it more formal.\\n\\n> old content")
    'Please make it more formal.'
    """
    if not body_text:
        return ""

    text = body_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()

    cut = len(lines)
    for i, line in enumerate(lines):
        if _QUOTE_PREFIX_RE.match(line):
            cut = i
            break

    text = "\n".join(lines[:cut])

    footer = _FOOTER_RE.search(text)
    if footer:
        text = text[: footer.start()]

    return text.strip()


def extract_last_review_draft(review_email: EmailMessage) -> str:
    """
    Extract the most recent draft text from a replied-to `[ALI REVIEW]` email.

    >>> msg = EmailMessage(
    ...     uid=1,
    ...     message_id="m1",
    ...     from_addr="a@b.com",
    ...     to_addrs=[],
    ...     cc_addrs=[],
    ...     subject="[ALI:v2] Hi",
    ...     body_text=(
    ...         "> ==============================\\n"
    ...         "> ALI'S RESPONSE - VERSION 1\\n"
    ...         "> first draft\\n"
    ...         "> ALI'S RESPONSE ENDED\\n"
    ...         "> ==============================\\n"
    ...         "> ALI'S RESPONSE - VERSION 2\\n"
    ...         "> second draft\\n"
    ...         "> ALI'S RESPONSE ENDED\\n"
    ...     ),
    ...     raw_bytes=b"",
    ... )
    >>> extract_last_review_draft(msg)
    'second draft'
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
