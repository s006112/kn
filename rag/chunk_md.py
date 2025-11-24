"""Helper functions for chunking Markdown files into JSON lines."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

MAX_CHARS = 2000  # maximum characters per chunk
MIN_CHARS = 80    # drop chunks smaller than this

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")


def markdown_files(base: Path) -> Iterable[Path]:
    """Yield all Markdown files under ``base``."""
    return base.rglob("*.md")


def split_markdown(text: str) -> list[str]:
    """Split Markdown ``text`` into semantic chunks."""
    chunks: list[str] = []
    section: list[str] = []
    size = 0
    in_code = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code  # toggle code block state
        if not in_code and stripped.startswith("#") and section:
            chunk = "\n".join(section).strip()
            if len(chunk) >= MIN_CHARS:
                chunks.append(chunk)
            section, size = [], 0
        section.append(line)
        size += len(line) + 1
        if not in_code and size >= MAX_CHARS:
            chunk = "\n".join(section).strip()
            if len(chunk) >= MIN_CHARS:
                chunks.append(chunk)
            section, size = [], 0
    if section:
        chunk = "\n".join(section).strip()
        if len(chunk) >= MIN_CHARS:
            chunks.append(chunk)
    return chunks


def process_file(path: Path, base: Path) -> list[dict]:
    """Return JSON serialisable chunks for ``path``."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed reading %s: %s", path, exc)
        return []
    parts = split_markdown(text)
    meta = {"relative_path": str(path.relative_to(base))}
    return [
        {
            "filename": path.name,
            "chunk_index": i + 1,
            "content": part,
            "metadata": meta,
        }
        for i, part in enumerate(parts)
    ]


def chunk_markdown_files(source_dir: Path, clean_dir: Path) -> Path:
    """Process all Markdown files under ``source_dir`` into ``clean_dir``."""
    clean_dir.mkdir(parents=True, exist_ok=True)
    outfile = clean_dir / f"md_{datetime.now().strftime('%y%m%d')}.jsonl"
    with outfile.open("w", encoding="utf-8") as f:
        for md in markdown_files(source_dir):
            logging.info("Processing %s", md)
            for record in process_file(md, source_dir):
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logging.info("Created %s", outfile)
    return outfile
