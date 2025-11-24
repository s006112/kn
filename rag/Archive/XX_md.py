#!/usr/bin/env python3
"""Copy Markdown references from the Obsidian vault.

This script parses ``W RAG.py`` in the local Obsidian folder, collects all
referenced Markdown files and copies them to the server at
``/root/email-rag/data/raw/attachments/md``.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Iterable

# use helper from the processing module to chunk notes after copying
from chunk_md import chunk_markdown_files

# Windows locations (source file and vault root)
# Windows paths for the Obsidian vault and the reference file
WIN_SOURCE = r"C:\Users\KN\iCloudDrive\iCloud~md~obsidian\OB Whisper\W RAG.md"
WIN_BASE = r"C:\Users\KN\iCloudDrive\iCloud~md~obsidian\OB Whisper"

# Destination directories on the Linux host
DEST_DIR = Path("/root/email-rag/data/raw/attachments/md")
CLEAN_DIR = Path("/root/email-rag/data/clean")
CLEAN_DIR = Path("/root/email-rag/data/clean")

# Regex matching Obsidian links ``[[file]]`` and Markdown ``[text](file.md)``
LINK_RE = re.compile(r"\[\[([^\]]+)\]\]|\[[^\]]*\]\(([^)]+\.md)\)", re.IGNORECASE)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")


def win_to_unix(path: str | Path) -> Path:
    """Translate a Windows path into the mounted Linux mount."""
    s = str(path)
    m = re.match(r"([A-Za-z]):[\\/]*(.*)", s)
    if m:
        drive, rest = m.groups()
        parts = Path(rest.replace("\\", "/")).parts
        return Path("/mnt", drive.lower(), *parts)
    return Path(s)


def extract_links(text: str) -> set[str]:
    """Return a set of Markdown link targets found in *text*."""
    links = set()
    for ref1, ref2 in LINK_RE.findall(text):
        link = ref1 or ref2
        if link:
            links.add(link.strip())  # normalise whitespace
    return links


def find_markdown(link: str, base: Path) -> Path | None:
    """Return the matching Markdown file for *link* or ``None``."""
    candidate = Path(link)
    if not candidate.suffix:
        candidate = candidate.with_suffix(".md")  # assume .md extension
    if not candidate.is_absolute():
        candidate = base / candidate  # resolve relative links inside vault
    candidate = win_to_unix(candidate)
    try:
        return candidate.resolve(strict=True)
    except FileNotFoundError:
        parent = candidate.parent
        if not parent.is_dir():
            return None
        name = candidate.name.lower()
        for child in parent.iterdir():
            if child.name.lower() == name:
                return child.resolve()
    return None


def copy_markdown(path: Path, dest: Path) -> None:
    """Copy ``path`` into ``dest`` if possible."""
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / path.name
    try:
        shutil.copy2(path, target)  # preserve metadata
        logging.info("Copied %s -> %s", path, target)
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed copying %s: %s", path, exc)


def collect_markdown(src_file: Path, base: Path, dest: Path) -> None:
    """Copy all Markdown links referenced in ``src_file``."""
    # Iterate over each extracted link and copy the resolved file
    for link in iter_links(src_file):
        md = find_markdown(link, base)
        if not md:
            logging.warning("Markdown not found: %s", link)
            continue
        copy_markdown(md, dest)


def iter_links(source: Path) -> Iterable[str]:
    """Yield all Markdown links referenced in ``source``."""
    try:
        text = source.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logging.error("Unable to read %s: %s", source, exc)
        return []
    return extract_links(text)


def main() -> None:
    src = win_to_unix(WIN_SOURCE)
    base = win_to_unix(WIN_BASE)
    collect_markdown(src, base, DEST_DIR)  # copy linked notes
    chunk_markdown_files(DEST_DIR, CLEAN_DIR)  # split notes into chunks


if __name__ == "__main__":
    main()