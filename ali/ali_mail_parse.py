"""
Used by:

- ali.ali_llm: input normalization and override extraction.
- ali.ali_fetch: review-subject matching and IMAP query constants.
- ali.ali_email: review-subject formatting and review-state parsing.
"""

from __future__ import annotations

import sys
from pathlib import Path
import re
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helper.utils_imap_types import EmailMessage  # type: ignore


# =============================================================================
# Review protocol constants (cross-module contract)
# =============================================================================

REVIEW_SUBJECT_MARKER = "[ALI:vX]"
REVIEW_SUBJECT_PATTERN = re.compile(r"\[ALI:v\d+\]", flags=re.IGNORECASE)
REVIEW_SUBJECT_IMAP_QUERY = REVIEW_SUBJECT_MARKER.replace("X]", "")
REVIEW_HEADER_LABEL = "ALI'S RESPONSE - VERSION"
REVIEW_FOOTER_LABEL = "ALI'S RESPONSE ENDED"
REVIEW_HEADER_LINE_TEMPLATE = (
    "=================   ALI'S RESPONSE - VERSION {version}   =================="
)
REVIEW_FOOTER_LINE = "====================   ALI'S RESPONSE ENDED   ====================="


# =============================================================================
# Review protocol parsing (ONLY protocol, nothing else)
# =============================================================================

_HEADER_RE = re.compile(
    rf"^\s*=*\s*{re.escape(REVIEW_HEADER_LABEL)}\s+(\d+)\s*=*\s*$",
    flags=re.MULTILINE,
)
_FOOTER_RE = re.compile(
    rf"^\s*=*\s*{re.escape(REVIEW_FOOTER_LABEL)}\s*=*\s*$",
    flags=re.MULTILINE,
)
_QUOTE_PREFIX_RE = re.compile(r"^\s*>+\s?(.*)$")


def _normalize_body(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_empty_ends(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _review_body_for_parsing(review_email: EmailMessage) -> str:
    body = _normalize_body(review_email.body_text or "")
    out: list[str] = []

    for line in body.splitlines():
        while True:
            m = _QUOTE_PREFIX_RE.match(line)
            if not m:
                break
            line = m.group(1)
        out.append(line)

    return "\n".join(out)


# =============================================================================
# Step 0 — Input normalization + override extraction (SINGLE SOURCE OF TRUTH)
# =============================================================================

_STEP0_WROTE_RE = re.compile(r"^On .* wrote:\s*$")
_STEP0_FORWARD_MARKERS = {
    "-----Original Message-----",
    "Begin forwarded message",
    "Forwarded message",
}
_STEP0_HEADER_PREFIXES = ("From:", "Sent:", "To:", "Subject:")


def normalize_email_input(
    email: EmailMessage,
    *,
    max_body_len: int | None = 12000,
) -> tuple[str, str]:
    subject = (email.subject or "").strip()
    body = _normalize_body((email.body_text or "").strip())

    if body:
        body = "\n".join(_strip_empty_ends(body.splitlines()))

    if max_body_len is not None and len(body) > max_body_len:
        body = body[:max_body_len]

    return subject, body


def extract_override_instructions(body: str) -> str:
    if not body:
        return ""

    lines = body.split("\n")
    header_run_start = 0
    header_run_len = 0

    for i, line in enumerate(lines):
        if line.startswith(">"):
            return "\n".join(lines[:i])
        if _STEP0_WROTE_RE.match(line):
            return "\n".join(lines[:i])
        if line in _STEP0_FORWARD_MARKERS:
            return "\n".join(lines[:i])

        if line.startswith(_STEP0_HEADER_PREFIXES):
            if header_run_len == 0:
                header_run_start = i
            header_run_len += 1
            if header_run_len >= 2:
                return "\n".join(lines[:header_run_start])
        else:
            header_run_len = 0

    return body


# =============================================================================
# Review protocol parsing (stable)
# =============================================================================

@dataclass(frozen=True)
class ReviewState:
    version: int
    draft: str


def extract_last_review_state(review_email: EmailMessage) -> ReviewState:
    body = _review_body_for_parsing(review_email)

    headers = list(_HEADER_RE.finditer(body))
    if not headers:
        raise ValueError("Cannot locate review header in review email")

    best = max(headers, key=lambda m: int(m.group(1)))
    version = int(best.group(1))

    remainder = body[best.end():].lstrip("\n")
    footer = _FOOTER_RE.search(remainder)
    draft = (remainder[: footer.start()] if footer else remainder).strip()

    return ReviewState(version=version, draft=draft)
