"""
helper_files.py -
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
- base_name -> candidate_path -> existence_check -> numbered_path
- file_path -> stat -> mode_or -> chmod

Invariants:
- `read_file_with_encodings` only retries on `UnicodeDecodeError`.
- `get_next_available_filename` always returns a path ending in `.txt`.
- `safe_rename` never overwrites an existing destination path.
- `release_text_file_permissions` only changes mode bits for `.txt` and `.md` paths.

Out of scope:
- Directory creation and path normalization.
- Atomic renames and cross-filesystem move guarantees.
- File locking, concurrency coordination, and permission ownership changes.
"""

import codecs
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Iterable, Tuple


DEFAULT_ENCODINGS = ("utf-8", "gbk", "gb2312", "gb18030", "big5")


def configure_logging(log_dir: str | os.PathLike[str]) -> None:
	os.makedirs(log_dir, exist_ok=True)
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s - %(levelname)s - %(message)s",
		datefmt="%H:%M:%S",
		handlers=[
			RotatingFileHandler(
				os.path.join(log_dir, "script.log"),
				maxBytes=1 * 1024 * 1024,
				backupCount=2,
				encoding="utf-8",
			),
			logging.StreamHandler(
				codecs.getwriter("utf-8")(sys.stdout.buffer)
				if getattr(sys.stdout, "buffer", None) is not None
				else sys.stdout
			),
		],
	)


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
