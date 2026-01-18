"""
Responsibility:
Chunks Markdown files into semantically grouped text blocks and writes them as JSONL records for downstream ingestion.

Used by:
* archive/XX_md.py

Pipelines:
- discover_files -> split_markdown -> build_records -> write_jsonl

Invariants:
- Code blocks delimited by lines starting with ``` are kept intact by suppressing heading-based splits while inside a code block.
- Chunks shorter than `MIN_CHARS` are dropped.
- A size-based flush occurs when the current section reaches `MAX_CHARS` outside code blocks.

Out of scope:
- Markdown parsing beyond simple heading/code-fence heuristics.
- Embedding, indexing, and retrieval.
"""

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
    """
    Purpose:
    Yield all Markdown files under a base directory.

    Inputs:
    - base: Root directory to search.

    Outputs:
    - An iterable of `Path` objects for files matching `*.md`.

    Side effects:
    - None.

    Failure modes:
    - None.
    """

    return base.rglob("*.md")


def split_markdown(text: str) -> list[str]:
    """
    Purpose:
    Split Markdown text into chunks using heading boundaries and size thresholds while preserving code blocks.

    Inputs:
    - text: Markdown source text.

    Outputs:
    - List of chunk strings (each at least `MIN_CHARS` characters).

    Side effects:
    - None.

    Failure modes:
    - None.
    """

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
    """
    Purpose:
    Read a Markdown file, split it into chunks, and convert them into JSON-serializable records.

    Inputs:
    - path: Markdown file path to read.
    - base: Base directory used to compute `relative_path` metadata.

    Outputs:
    - List of dict records with `filename`, `chunk_index`, `content`, and `metadata`.

    Side effects:
    - Logs an error and returns `[]` when file reading fails.

    Failure modes:
    - Returns `[]` when `path.read_text` raises.
    """

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
    """
    Purpose:
    Process all Markdown files under `source_dir` and write a single JSONL output file under `clean_dir`.

    Inputs:
    - source_dir: Root directory containing Markdown files.
    - clean_dir: Output directory for the generated JSONL file.

    Outputs:
    - Path to the created JSONL file.

    Side effects:
    - Creates `clean_dir` if needed.
    - Writes JSONL records to disk and logs progress.

    Failure modes:
    - Propagates filesystem exceptions when output cannot be created/written.
    """

    clean_dir.mkdir(parents=True, exist_ok=True)
    outfile = clean_dir / f"md_{datetime.now().strftime('%y%m%d')}.jsonl"
    with outfile.open("w", encoding="utf-8") as f:
        for md in markdown_files(source_dir):
            logging.info("Processing %s", md)
            for record in process_file(md, source_dir):
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logging.info("Created %s", outfile)
    return outfile
