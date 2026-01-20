#!/usr/bin/env python3
"""
Responsibility:
Convert split text files into page-scoped JSONL blocks with an injected UL
standard/page prefix and basic size counters.

JSON metadata schema (per line):
- block_id: str, "{file_id}_p{page:04d}"
- file_id: str, source stem (e.g., "s935_10.page_splited" -> "s935_10")
- page: int, page number from <<<PAGE_BREAK_N>>> markers
- char: int, length of the text field
- word: int, whitespace-split token count of the text field
- text: str, "UL {standard_number}, page {page} " + page body

Pipelines:
- read_files -> split_pages -> inject_prefix -> write_jsonl

Invariants:
- Each output line is a JSON object with keys: block_id, file_id, page, char,
  word, text.
- The text field always includes a "UL {standard_number}, page {page}" prefix.
- Page numbers come only from <<<PAGE_BREAK_N>>> markers and start at 0.

Out of scope:
- Vectorization or embedding metadata.
- Downstream schemas with doc_type, doc_id, or vector fields.
"""
from __future__ import annotations
from pathlib import Path
import json, re, sys

# === 配置 ===
TXT_SPLITTED_DIR = Path("data/standard/txt_splitted") 
OUTPUT = Path("data/standard/json")
IN_SUFFIX = ".page_splited"
STD_BLOCK_SUFFIX = ".chunks.jsonl"
PAGE_RE = re.compile(r"^<<<PAGE_BREAK_(\d+)>>>$")

def main() -> None:
    """
    Purpose:
    Walk TXT_SPLITTED_DIR for *.page_splited files and emit per-page JSONL
    blocks into OUTPUT.
    Inputs:
    - TXT_SPLITTED_DIR
    - IN_SUFFIX
    - PAGE_RE
    Outputs:
    - JSONL files in OUTPUT with one block per page.
    Side effects:
    - Creates OUTPUT directories as needed.
    - Writes files to disk and prints progress/errors.
    Failure modes:
    - Exits with status 1 if TXT_SPLITTED_DIR is missing.
    - Propagates I/O errors from file reads or writes.
    """
    if not TXT_SPLITTED_DIR.exists():
        print(f"[ERROR] {TXT_SPLITTED_DIR} not found", file=sys.stderr)
        sys.exit(1)

    OUTPUT.mkdir(parents=True, exist_ok=True)

    for src in sorted(TXT_SPLITTED_DIR.rglob(f"*{IN_SUFFIX}")):
        dst = OUTPUT / src.with_suffix(STD_BLOCK_SUFFIX).name
        file_id = src.stem   # 例如 s935_10.page_splited
        print(f"[INFO] {src} -> {dst}")

        current_page = 0
        buf = []

        with dst.open("w", encoding="utf-8") as out:
            def flush():
                nonlocal buf, current_page
                if not buf:
                    return
                # 从文件名中抽取标准号，如 s1581_4 -> 1581, s50E_4 -> 50E
                m_std = re.match(r"^s(\d+[A-Za-z]?)_\d+$", file_id)
                standard_number = m_std.group(1) if m_std else file_id
                text_body = " ".join(buf).strip()
                if not text_body:
                    buf = []
                    return
                # 为每个块注入标准编号与页码前缀
                injected_prefix = f"UL {standard_number}, page {current_page} "
                text = injected_prefix + text_body
                block = {
                    "block_id": f"{file_id}_p{current_page:04d}",
                    "file_id": file_id,
                    "page": current_page,
                    "char": len(text),
                    "word": len([t for t in text.split() if t]),
                    "text": text,
                }
                out.write(json.dumps(block, ensure_ascii=False) + "\n")
                buf = []

            for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = PAGE_RE.match(line.strip())
                if m:
                    flush()
                    current_page = int(m.group(1))
                else:
                    buf.append(line)
            flush()

if __name__ == "__main__":
    main()
