from __future__ import annotations

import re
from dataclasses import dataclass

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
# Parsing regex (implementation details)
#
# NOTE:
# This module is intentionally optimized ONLY for:
# - Mozilla Thunderbird
# - Apple Mail (macOS / iOS)
#
# Other client formats (e.g. Outlook, Gmail Web UI) are handled
# on a best-effort basis ONLY. Do NOT expand regex coverage unless
# a real reviewer uses that client.
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
_SIGNATURE_DELIM_RE = re.compile(r"^\s*--\s*$")
_WROTE_RE = re.compile(r"^\s*On .+wrote:\s*$", flags=re.IGNORECASE)
_FORWARDED_RE = re.compile(
    r"^\s*(?:Begin forwarded message:|-{2,}\s*(?:Original Message|Forwarded message)\s*-{2,})\s*$",
    flags=re.IGNORECASE,
)
_STEP0_WROTE_RE = re.compile(r"^On .* wrote:\s*$")
_STEP0_FORWARD_MARKERS = {
    "-----Original Message-----",
    "Begin forwarded message",
    "Forwarded message",
}
_STEP0_HEADER_PREFIXES = ("From:", "Sent:", "To:", "Subject:")


# =============================================================================
# Internal helpers
# =============================================================================

def _normalize_body(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _review_body_for_parsing(review_email: EmailMessage) -> str:
    """
    Normalize line endings and fully dequote email body
    for consistent marker parsing.
    """
    body = _normalize_body(review_email.body_text or "")
    dequoted: list[str] = []

    for line in body.splitlines():
        while True:
            m = _QUOTE_PREFIX_RE.match(line)
            if not m:
                break
            line = m.group(1)
        dequoted.append(line)

    return "\n".join(dequoted)


# =============================================================================
# Step 0 helpers (input normalization + conservative override extraction)
# =============================================================================

def normalize_email_input(
    email: EmailMessage,
    *,
    max_body_len: int | None = 12000,
) -> tuple[str, str]:
    subject_norm = (email.subject or "").strip()
    body_norm = _normalize_body((email.body_text or "").strip())

    if body_norm:
        lines = body_norm.split("\n")
        start = 0
        end = len(lines)
        while start < end and lines[start].strip() == "":
            start += 1
        while end > start and lines[end - 1].strip() == "":
            end -= 1
        body_norm = "\n".join(lines[start:end])

    if max_body_len is not None and len(body_norm) > max_body_len:
        body_norm = body_norm[:max_body_len]

    return subject_norm, body_norm


def extract_override_instructions(body_norm: str) -> str:
    if not body_norm:
        return ""

    lines = body_norm.split("\n")
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

    return body_norm


# =============================================================================
# Public parsing API
# =============================================================================

@dataclass(frozen=True)
class ReviewState:
    version: int
    draft: str


def extract_last_review_state(review_email: EmailMessage) -> ReviewState:
    """
    Extract the canonical last review state from a review email.
    """
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


def extract_sender_override(body_text: str) -> str | None:
    """
    Extract sender-written override instructions.

    Semantics:
    - Only consider text ABOVE quoted / forwarded history.
    - Empty or auto-signature-only replies are treated as REJECT.
    """
    if not body_text:
        return ""

    lines = _normalize_body(body_text).splitlines()

    cut = len(lines)
    for i, line in enumerate(lines):
        if (
            _QUOTE_PREFIX_RE.match(line)
            or _WROTE_RE.match(line)
            or _FORWARDED_RE.match(line)
        ):
            cut = i
            break

    text = "\n".join(lines[:cut])

    footer = _FOOTER_RE.search(text)
    if footer:
        text = text[: footer.start()]

    extracted = text.strip()
    if not extracted:
        return ""

    # Strip RFC 3676-style signature
    out_lines = []
    for line in extracted.splitlines():
        if _SIGNATURE_DELIM_RE.match(line):
            break
        out_lines.append(line)

    extracted = "\n".join(out_lines).strip()
    if not extracted:
        return ""

    # Common Apple auto-signatures → treated as empty (REJECT)
    if extracted.lower() in {
        "sent from my iphone",
        "sent from my ipad",
        "sent from my ipod",
        "sent from my mac",
    }:
        return ""

    # Hard reject: header-like lines or heavy quoted density.
    header_like_re = re.compile(
        r"^(from:|sent:|to:|subject:|-{2,}\s*original message\s*-{2,})",
        flags=re.IGNORECASE,
    )
    header_like = 0
    lines = extracted.splitlines()
    for line in lines:
        if header_like_re.match(line.strip()):
            header_like += 1
            if header_like >= 2:
                return None
    if lines:
        quote_lines = sum(1 for line in lines if line.lstrip().startswith(">"))
        if quote_lines / len(lines) > 0.4:
            return None

    return extracted
