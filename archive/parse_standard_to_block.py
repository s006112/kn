#!/usr/bin/env python3
"""
standard_to_jsonl.py
Convert standard PDF files to canonical JSONL format.
Also write per-file JSONL for debugging / audit.
"""

from pathlib import Path
import json
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parse_raw_to_jsonl import parse_pdf_bytes_to_canonical_blocks

RAW_PDF_DIR = Path("data/standard/pdf")
PER_FILE_DIR = Path("data/standard/jsonl")
CANONICAL_JSONL = Path("data/standard_chunks.jsonl")

def list_pdfs(root: Path):
    return sorted(root.rglob("*.pdf"))

def main():
    CANONICAL_JSONL.parent.mkdir(parents=True, exist_ok=True)
    PER_FILE_DIR.mkdir(parents=True, exist_ok=True)

    with CANONICAL_JSONL.open("w", encoding="utf-8") as canonical_out:
        for pdf in list_pdfs(RAW_PDF_DIR):
            doc_id = pdf.stem
            print(f"[INFO] Parsing PDF: {pdf}")

            per_file_path = PER_FILE_DIR / f"{doc_id}.blocks.jsonl"

            with per_file_path.open("w", encoding="utf-8") as per_file_out:
                for block in parse_pdf_bytes_to_canonical_blocks(
                    pdf.read_bytes(),
                    str(pdf),
                    doc_id,
                ):
                    line = json.dumps(block, ensure_ascii=False) + "\n"

                    # 写 canonical
                    canonical_out.write(line)

                    # 写 per-file
                    per_file_out.write(line)

            print(f"[INFO] Finished {pdf}")
            print(f"[INFO] Per-file JSONL: {per_file_path}")

    print(f"[DONE] Canonical JSONL written to {CANONICAL_JSONL}")


if __name__ == "__main__":
    main()
