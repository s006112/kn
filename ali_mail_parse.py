from __future__ import annotations

import re
from dataclasses import dataclass

from helper.utils_imap_types import EmailMessage  # type: ignore


# =============================================================================
# Review subject protocol (cross-module contract)
# =============================================================================

REVIEW_SUBJECT_MARKER = "[ALI:vX]"
REVIEW_SUBJECT_PATTERN = re.compile(r"\[ALI:v\d+\]", flags=re.IGNORECASE)
REVIEW_SUBJECT_IMAP_QUERY = REVIEW_SUBJECT_MARKER.replace("X]", "")


# =============================================================================
# Parsing regex (implementation details)
# =============================================================================

# Be tolerant to mail-client reformatting:
# - "====" separators may be on the same line as header/footer
# - surrounding whitespace may be trimmed or altered
_HEADER_RE = re.compile(
    r"^\s*=*\s*ALI'S RESPONSE - VERSION\s+(\d+)\s*=*\s*$",
    flags=re.MULTILINE,
)
_FOOTER_RE = re.compile(
    r"^\s*=*\s*ALI'S RESPONSE ENDED\s*=*\s*$",
    flags=re.MULTILINE,
)
_QUOTE_PREFIX_RE = re.compile(r"^\s*>+\s?(.*)$")


# =============================================================================
# Internal helpers
# =============================================================================

def _review_body_for_parsing(review_email: EmailMessage) -> str:
    """
    Normalize line endings and fully dequote email body
    for consistent marker parsing.
    """
    body_text = (review_email.body_text or "").replace("\r\n", "\n").replace("\r", "\n")

    dequoted_lines: list[str] = []
    for line in body_text.splitlines():
        # strip all leading quote prefixes (>, >>, etc.)
        while True:
            match = _QUOTE_PREFIX_RE.match(line)
            if not match:
                break
            line = match.group(1)
        dequoted_lines.append(line)

    return "\n".join(dequoted_lines)


# =============================================================================
# Public parsing API
# =============================================================================

@dataclass(frozen=True)
class ReviewState:
    """
    Canonical representation of the LAST review state
    found in an email thread.
    """
    version: int
    draft: str


def extract_last_review_state(review_email: EmailMessage) -> ReviewState:
    """
    Extract the canonical last review state from a review email.

    Semantics:
    - Dequote the email body.
    - Locate the highest-version 'ALI'S RESPONSE - VERSION N' header.
    - Extract its version and corresponding draft block.
    """
    body = _review_body_for_parsing(review_email)

    headers = list(_HEADER_RE.finditer(body))
    if not headers:
        raise ValueError("Cannot locate review header in review email")

    best_header = max(headers, key=lambda match: int(match.group(1)))
    version = int(best_header.group(1))

    remainder = body[best_header.end():].lstrip("\n")
    footer = _FOOTER_RE.search(remainder)

    draft = (
        remainder[: footer.start()] if footer else remainder
    ).strip()

    return ReviewState(version=version, draft=draft)


def extract_sender_override(body_text: str) -> str:
    """
    Extract sender-written override instructions.

    Rules:
    - Only consider text ABOVE quoted history.
    - Quoted lines ("> ...") mark the start of history.
    - Footer markers are ignored if present.

    Examples:
    >>> extract_sender_override("> old line\\n> another old line")
    ''
    >>> extract_sender_override("Please make it more formal.\\n\\n> old content")
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
