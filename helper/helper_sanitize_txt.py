"""
Compatibility wrapper for `helper.helper_sanitize`.

Existing callers import `sanitize_text` from this module. The implementation now lives in
`helper/helper_sanitize.py`.
"""

from __future__ import annotations

from helper.helper_sanitize import (  # noqa: F401
    CHAR_REPLACEMENTS,
    CLEAN_REGEXES_GENERAL,
    sanitize_text,
)

__all__ = [
    "CHAR_REPLACEMENTS",
    "CLEAN_REGEXES_GENERAL",
    "sanitize_text",
]

