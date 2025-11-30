from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_DOTENV_LOADED = False


def load_env(dotenv_path: str | Path | None = None) -> None:
    """Load environment variables from .env once."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    if dotenv_path is None:
        load_dotenv()
    else:
        load_dotenv(dotenv_path)
    _DOTENV_LOADED = True


def get_log_level(default: str = "INFO") -> int:
    """Return logging level from LOG_LEVEL env or default."""
    level_name = os.getenv("LOG_LEVEL", default).upper()
    return getattr(logging, level_name, logging.INFO)


def configure_logging(logger_name: str = "app", default_level: str = "INFO") -> logging.Logger:
    """Configure root logging once and return a named logger."""
    level = get_log_level(default_level)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    return logging.getLogger(logger_name)


def get_env_flag(name: str, default: bool = False) -> bool:
    """Interpret an env var as boolean; only 'true' (case-insensitive) is True."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() == "true"


def load_prompt_text(base_dir: Path, filename: str) -> str | None:
    """Load a UTF-8 prompt text file from base_dir; return None on failure."""
    try:
        return (base_dir / filename).read_text("utf-8")
    except Exception:
        return None


__all__ = ["load_env", "get_log_level", "configure_logging", "get_env_flag", "load_prompt_text"]
