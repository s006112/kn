"""
utils_files.py -
Shared helpers for text file IO and filename management used by Whisper pipeline code.

Used by:
* w/p_audio.py
* w/p_distill.py
* w/p_extract.py
* p.py
* w/p_pipelines.py
* w/p_pretext.py
* w/p_ttml.py
* w/utils_md.py
* w/utils_unlink.py

Pipelines:
- file_path -> encoding_candidates -> open_attempts -> text
- filename -> script_dir -> prompt_path -> text
- base_name -> candidate_path -> existence_check -> numbered_path
- file_path -> stat -> mode_or -> chmod

Invariants:
- `read_file_with_encodings` only retries on `UnicodeDecodeError`.
- `read_prompt_file` reads from this module's directory and strips outer whitespace.
- `get_next_available_filename` always returns a path ending in `.txt`.
- `safe_rename` never overwrites an existing destination path.
- `release_text_file_permissions` only changes mode bits for `.txt` and `.md` paths.

Out of scope:
- Directory creation and path normalization.
- Atomic renames and cross-filesystem move guarantees.
- File locking, concurrency coordination, and permission ownership changes.
"""

import logging
import os
from typing import Iterable, Tuple


DEFAULT_ENCODINGS = ("utf-8", "gbk", "gb2312", "gb18030", "big5")


def read_file_with_encodings(
    file_path: str, encodings: Iterable[str] | None = None
) -> Tuple[str, str]:
    """Read a text file using the first encoding that successfully decodes it."""
    candidates = tuple(encodings) if encodings else DEFAULT_ENCODINGS
    for enc in candidates:
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to read file: {file_path}")


def read_prompt_file(filename: str) -> str:
    """Load and strip a UTF-8 prompt file located beside this module."""
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
    """Return the next available suffixed `.txt` path under `base_path`."""
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
    """Rename `old_path` to `new_path` when the destination does not exist."""
    try:
        if not os.path.exists(new_path):
            os.rename(old_path, new_path)
            return new_path
        return old_path
    except Exception as exc:
        logging.error("Rename failed %s -> %s: %s", old_path, new_path, exc)
        return old_path


def release_text_file_permissions(path: os.PathLike | str | None) -> None:
    """Make `.txt` and `.md` files editable by adding read/write permission bits."""
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
