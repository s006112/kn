"""
Shared helpers for text file IO and filename management used by Whisper pipeline code.

Used by:
* whisper/p_audio.py
* whisper/p_distill.py
* whisper/p_extract.py
* whisper/p_orchestrator.py
* whisper/p_pipelines.py
* whisper/p_pretext.py
* whisper/p_ttml.py
* whisper/utils_md.py
* whisper/utils_unlink.py

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
    """
    Purpose:
    - Read a text file using the first encoding that successfully decodes it.
    Inputs:
    - file_path: Filesystem path to open and read.
    - encodings: Optional iterable of encoding names; when omitted, uses `DEFAULT_ENCODINGS`.
    Outputs:
    - (content, encoding_used): File contents and the encoding that decoded it.
    Side effects:
    - Opens and reads the file from disk.
    Failure modes:
    - Raises `ValueError` if all candidate encodings raise `UnicodeDecodeError`.
    - Propagates `OSError` subclasses from `open` (for example `FileNotFoundError`).
    """
    candidates = tuple(encodings) if encodings else DEFAULT_ENCODINGS
    for enc in candidates:
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to read file: {file_path}")


def read_prompt_file(filename: str) -> str:
    """
    Purpose:
    - Load a UTF-8 prompt file located in the same directory as this module.
    Inputs:
    - filename: Filename relative to this module's directory.
    Outputs:
    - The file contents with leading/trailing whitespace removed.
    Side effects:
    - Opens and reads a file from disk.
    - Logs an error message on failure.
    Failure modes:
    - Raises `ValueError` on any read error, chaining the original exception.
    """
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
    """
    Purpose:
    - Generate a `.txt` path under `base_path` that does not already exist.
    Inputs:
    - base_path: Directory to place the file in.
    - base_name: Base filename without extension.
    - suffix: Suffix appended to `base_name` before numbering (default `_e`).
    Outputs:
    - A full filesystem path ending in `.txt` that does not exist at call time.
    Side effects:
    - Performs filesystem existence checks.
    Failure modes:
    - May loop until a non-existing name is found.
    """
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
    """
    Purpose:
    - Rename `old_path` to `new_path` only when `new_path` does not exist.
    Inputs:
    - old_path: Existing filesystem path to rename.
    - new_path: Destination path; must not exist to perform the rename.
    Outputs:
    - The path that should be treated as the current location (`new_path` on success,
      otherwise `old_path`).
    Side effects:
    - May rename a file on disk.
    - Logs an error message on failure.
    Failure modes:
    - Returns `old_path` if `new_path` already exists.
    - Returns `old_path` if an exception occurs during `os.rename`.
    """
    try:
        if not os.path.exists(new_path):
            os.rename(old_path, new_path)
            return new_path
        return old_path
    except Exception as exc:
        logging.error("Rename failed %s -> %s: %s", old_path, new_path, exc)
        return old_path


def release_text_file_permissions(path: os.PathLike | str | None) -> None:
    """
    Purpose:
    - Ensure `.txt` and `.md` files remain editable by OR-ing world read/write bits.
    Inputs:
    - path: Filesystem path to a file (or `None` to do nothing).
    Outputs:
    - None.
    Side effects:
    - Reads file mode bits via `os.stat`.
    - May modify file permissions via `os.chmod`.
    - Logs a warning on permission update failures.
    Failure modes:
    - Returns without raising if `path` is falsey, has a non-text extension, or the file
      is not found.
    - Logs and returns on other `OSError` cases during stat/chmod.
    """
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
