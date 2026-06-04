#!/usr/bin/env python3
"""
standard_3_sanitized_to_jsonl.py

Responsibility:
Convert split text files into page-scoped JSONL blocks with an injected UL
standard/page prefix and basic size counters.

JSON metadata schema (per line):
- block_id: str, "{doc_id}_p{page_id}"
- doc_id: str, source stem (e.g., "s935_10.page_splited" -> "s935_10")
- page: int | str, numeric page number or non-numeric page label such as "T2"
- char: int, length of the text field
- word: int, whitespace-split token count of the text field
- text: str, "UL {standard_number}, page {page} " + page body

Pipelines:
- read_files -> split_pages -> inject_prefix -> write_jsonl

Invariants:
- Each output line is a JSON object with keys: block_id, doc_id, page, char,
  word, text.
- The text field always includes a "UL {standard_number}, page {page}" prefix.
- Numeric page labels remain integers and preserve zero-padded block IDs.
- Non-numeric page labels remain strings.

Out of scope:
- Vectorization or embedding metadata.
- Downstream schemas with doc_type or vector fields.
"""

from __future__ import annotations

from pathlib import Path
import json
import re
import sys

# === 配置 ===

TXT_SPLITTED_DIR = Path("data/standard/txt_splitted")
OUTPUT = Path("data/standard/jsonl")
IN_SUFFIX = ".page_splited"
STD_BLOCK_SUFFIX = "_chunks.jsonl"
PAGE_RE = re.compile(r"^<<<PAGE_BREAK_((?:\d+|T\d+))>>>$", flags=re.IGNORECASE)


def parse_page_label(label: str) -> int | str:
    return int(label) if label.isdigit() else label.upper()


def format_page_id(page: int | str) -> str:
    return f"{page:04d}" if isinstance(page, int) else page


def main() -> None:
    """
    Purpose:
    Walk TXT_SPLITTED_DIR for *.page_splited files and emit per-page JSONL
    blocks into OUTPUT.
    """
    if not TXT_SPLITTED_DIR.exists():
        print(f"[ERROR] {TXT_SPLITTED_DIR} not found", file=sys.stderr)
        sys.exit(1)

    OUTPUT.mkdir(parents=True, exist_ok=True)

    for src in sorted(TXT_SPLITTED_DIR.rglob(f"*{IN_SUFFIX}")):
        dst = OUTPUT / f"{src.stem}{STD_BLOCK_SUFFIX}"
        doc_id = src.stem
        print(f"[INFO] {src} -> {dst}")

        current_page: int | str = 0
        buf: list[str] = []

        with dst.open("w", encoding="utf-8") as out:
            def flush() -> None:
                nonlocal buf, current_page

                if not buf:
                    return

                m_std = re.match(r"^s(\d+[A-Za-z]?)_\d+$", doc_id)
                standard_number = m_std.group(1) if m_std else doc_id
                text_body = " ".join(buf).strip()

                if not text_body:
                    buf = []
                    return

                text = f"UL {standard_number}, page {current_page} {text_body}"
                block = {
                    "page": current_page,
                    "char": len(text),
                    "word": len(text.split()),
                    "file_type": "pdf",

                    "doc_id": doc_id,
                    "block_id": f"{doc_id}_{format_page_id(current_page)}",

                    "text": text,
                }
                out.write(json.dumps(block, ensure_ascii=False) + "\n")
                buf = []

            for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = PAGE_RE.match(line.strip())
                if m:
                    flush()
                    current_page = parse_page_label(m.group(1))
                else:
                    buf.append(line)

            flush()


if __name__ == "__main__":
    main()
