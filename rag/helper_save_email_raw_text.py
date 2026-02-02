"""
helper_save_email_raw_text.py

Use by:
- rag/helper_parse_email_to_raw_based.py
- rag/helper_parse_email_to_raw_enhanced.py

Responsibility:
Persist raw email "text part" content to disk for debugging/inspection.

Invariants:
- Writes under project-local `log/data/email_raw/`.
- Uses UTF-8 with a safe error strategy.
- Uses a filesystem-safe filename derived from the provided `email_id`.

Out of scope:
- Email parsing or content sanitization.
"""

from __future__ import annotations

from pathlib import Path
import re


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _project_root_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_filename_component(value: str, *, max_len: int = 160) -> str:
    s = (value or "").strip()
    s = s.strip("<>")  # common in Message-ID
    s = _FILENAME_SAFE_RE.sub("_", s)
    s = s.strip(" ._-")
    if not s:
        s = "email"
    return s[:max_len]


def raw_email_text_path(*, email_id: str, root_dir: Path | None = None) -> Path:
    base = _project_root_dir() if root_dir is None else Path(root_dir)
    out_dir = base / "log" / "data" / "eml"
    filename = f"{_safe_filename_component(str(email_id))}.txt"
    return out_dir / filename


def save_raw_email_text(*, email_id: str, content: str, root_dir: Path | None = None) -> Path | None:
    """
    Persist the raw email text content to disk.

    Returns the written file path, or None if `content` is falsy or writing fails.
    """
    if not content:
        return None

    path = raw_email_text_path(email_id=email_id, root_dir=root_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", errors="replace")
    except Exception:
        return None
    return path

