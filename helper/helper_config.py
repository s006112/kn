"""
Responsibility:
Small configuration helpers for environment loading, logging setup, and safe access to environment variables and prompt files.

Used by:
* gui_rendering.py
* gui_weekly_summary.py
* core_per_report.py
* core_so_import.py
* ali/ali_email.py
* ali_email/ali_fetch.py
* ali_email/ali_llm.py
* ali_email/ali_send.py
* helper/utils_imap_config.py
* helper/utils_odoo.py
* tool/test_email_send_dummy.py
* archive/ali_state.py

Pipelines:
- load_env -> configure_logging -> read_env -> load_prompt

Invariants:
- `load_env` loads dotenv at most once per process.
- `configure_logging` only calls `basicConfig` when the root logger has no handlers.
- `get_env_flag` treats only the string `"true"` (case-insensitive) as true.
- `get_env_int` and `get_env_str` return provided defaults on missing/invalid values.

Out of scope:
- Complex configuration schemas and validation.
- Secret management beyond reading environment variables.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_DOTENV_LOADED = False


def load_env(dotenv_path: str | Path | None = None) -> None:
    """Load `.env` once per process. 不重复加载。"""

    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    if dotenv_path is None:
        load_dotenv()
    else:
        load_dotenv(dotenv_path)
    _DOTENV_LOADED = True


def get_log_level(default: str = "INFO") -> int:
    """Resolve `LOG_LEVEL`; invalid names fall back to `logging.INFO`."""

    level_name = os.getenv("LOG_LEVEL", default).upper()
    return getattr(logging, level_name, logging.INFO)


def configure_logging(logger_name: str = "app", default_level: str = "INFO") -> logging.Logger:
    """Set basic logging if needed, then return the named logger."""

    level = get_log_level(default_level)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    return logging.getLogger(logger_name)


def get_env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env var; only `"true"` counts as true."""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() == "true"


def get_env_int(name: str, default: int) -> int:
    """Read an int env var; 缺失或解析失败时返回默认值。"""

    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_str(name: str, default: str) -> str:
    """Read a string env var; missing or empty values use `default`."""

    value = os.getenv(name)
    if not value:
        return default
    return value


def load_prompt_text(base_dir: Path, filename: str) -> str | None:
    """Read a UTF-8 prompt file; return `None` if it cannot be read."""

    try:
        return (base_dir / filename).read_text("utf-8")
    except Exception:
        return None


__all__ = [
    "load_env",
    "get_log_level",
    "configure_logging",
    "get_env_flag",
    "get_env_int",
    "get_env_str",
    "load_prompt_text",
]
