"""Shared helpers for reading/writing text files and managing filenames."""

import logging
import os
from typing import Iterable, Tuple


DEFAULT_ENCODINGS = ("utf-8", "gbk", "gb2312", "gb18030", "big5")


def read_file_with_encodings(
    file_path: str, encodings: Iterable[str] | None = None
) -> Tuple[str, str]:
    """Read text file trying multiple encodings, return (content, encoding)."""
    candidates = tuple(encodings) if encodings else DEFAULT_ENCODINGS
    for enc in candidates:
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to read file: {file_path}")


def read_prompt_file(filename: str) -> str:
    """Read a prompt file from the script directory."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    prompt_path = os.path.join(script_dir, filename)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as exc:
        logging.error("Error reading prompt file %s: %s", filename, exc)
        raise ValueError(
            f"Failed to load {filename}. Ensure the file exists in the script directory."
        ) from exc


def get_next_available_filename(
    base_path: str, base_name: str, suffix: str = "_e"
) -> str:
    """Generate the next available filename with optional suffix and counter."""
    initial_path = os.path.join(base_path, f"{base_name}{suffix}.txt")
    if not os.path.exists(initial_path):
        return initial_path
    counter = 1
    while True:
        numbered_path = os.path.join(
            base_path, f"{base_name}{suffix}_{counter}.txt"
        )
        if not os.path.exists(numbered_path):
            return numbered_path
        counter += 1


def safe_rename(old_path: str, new_path: str) -> str:
    """Safely rename a file, avoiding overwrites."""
    try:
        if not os.path.exists(new_path):
            os.rename(old_path, new_path)
            return new_path
        return old_path
    except Exception as exc:
        logging.error("Rename failed %s -> %s: %s", old_path, new_path, exc)
        return old_path


def release_text_file_permissions(path: os.PathLike | str | None) -> None:
    """Ensure .txt/.md files stay editable by granting world read/write permissions."""
    if not path:
        return
    file_path = os.fspath(path)
    if not file_path.lower().endswith((".txt", ".md")):
        return
    try:
        current_mode = os.stat(file_path).st_mode
        desired_mode = current_mode | 0o666
        if desired_mode != current_mode:
            os.chmod(file_path, desired_mode)
    except FileNotFoundError:
        return
    except OSError as exc:
        logging.warning("Unable to release permissions for %s: %s", file_path, exc)
