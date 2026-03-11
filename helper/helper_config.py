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
    """
    Purpose:
    Load environment variables from a `.env` file at most once.

    Inputs:
    - dotenv_path: Optional path to a dotenv file; when omitted, uses default discovery.

    Outputs:
    - None.

    Side effects:
    - Calls `dotenv.load_dotenv` and updates `os.environ`.
    - Sets the module-level `_DOTENV_LOADED` flag.

    Failure modes:
    - Propagates exceptions raised by `load_dotenv` for invalid paths or read errors.
    """

    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    if dotenv_path is None:
        load_dotenv()
    else:
        load_dotenv(dotenv_path)
    _DOTENV_LOADED = True


def get_log_level(default: str = "INFO") -> int:
    """
    Purpose:
    Resolve the effective logging level from `LOG_LEVEL` or a default value.

    Inputs:
    - default: Fallback level name used when `LOG_LEVEL` is not set.

    Outputs:
    - An integer logging level (e.g. `logging.INFO`).

    Side effects:
    - None.

    Failure modes:
    - Unknown level names fall back to `logging.INFO`.
    """

    level_name = os.getenv("LOG_LEVEL", default).upper()
    return getattr(logging, level_name, logging.INFO)


def configure_logging(logger_name: str = "app", default_level: str = "INFO") -> logging.Logger:
    """
    Purpose:
    Configure basic logging formatting/level once and return a named logger.

    Inputs:
    - logger_name: Name passed to `logging.getLogger`.
    - default_level: Default log level name when `LOG_LEVEL` is missing.

    Outputs:
    - A `logging.Logger` instance for `logger_name`.

    Side effects:
    - Calls `logging.basicConfig` when the root logger has no handlers.

    Failure modes:
    - None.
    """

    level = get_log_level(default_level)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    return logging.getLogger(logger_name)


def get_env_flag(name: str, default: bool = False) -> bool:
    """
    Purpose:
    Interpret an environment variable as a boolean.

    Inputs:
    - name: Environment variable name.
    - default: Value returned when the variable is missing.

    Outputs:
    - `True` only when the value equals `"true"` (case-insensitive); otherwise `False` or `default`.

    Side effects:
    - None.

    Failure modes:
    - None.
    """

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() == "true"


def get_env_int(name: str, default: int) -> int:
    """
    Purpose:
    Read an integer from an environment variable with a safe default.

    Inputs:
    - name: Environment variable name.
    - default: Value returned when the variable is missing or cannot be parsed.

    Outputs:
    - Parsed integer value or `default`.

    Side effects:
    - None.

    Failure modes:
    - Returns `default` when parsing fails (no exception is raised).
    """

    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_str(name: str, default: str) -> str:
    """
    Purpose:
    Read a string from an environment variable with a safe default.

    Inputs:
    - name: Environment variable name.
    - default: Value returned when the variable is missing or empty.

    Outputs:
    - Environment variable value or `default`.

    Side effects:
    - None.

    Failure modes:
    - None.
    """

    value = os.getenv(name)
    if not value:
        return default
    return value


def load_prompt_text(base_dir: Path, filename: str) -> str | None:
    """
    Purpose:
    Read a UTF-8 prompt text file from a base directory.

    Inputs:
    - base_dir: Directory containing the prompt file.
    - filename: Prompt filename relative to `base_dir`.

    Outputs:
    - File contents as a string, or `None` when reading fails.

    Side effects:
    - Reads from the filesystem.

    Failure modes:
    - Returns `None` on any exception.
    """

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
