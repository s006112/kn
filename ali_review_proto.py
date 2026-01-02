from __future__ import annotations

import re


# Review thread protocol (single source of truth)

REVIEW_SUBJECT_MARKER = "[vX]"
REVIEW_SUBJECT_PATTERN = re.compile(r"\[v\d+\]")
REVIEW_SUBJECT_IMAP_QUERY = REVIEW_SUBJECT_MARKER.replace("X]", "")

INTERNAL_REVIEW_ANCHOR = "[ALI INTERNAL REVIEW — NOT FOR CUSTOMER]"
ORIGINAL_MESSAGE_MARKER = "-----Original Message-----"
