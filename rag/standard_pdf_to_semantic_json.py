#!/usr/bin/env python3
"""
standard_pdf_to_semantic_json.py
Convert standard PDF files to semantic JSON format.
"""


from pathlib import Path
import json
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_parse_pdf_to_raw import get_pdf_page_blocks
from helper.helper_pdf_to_semantics_schema import build_semantic_schema

RAW_PDF_DIR = Path("data/standard/pdf")
OUTPUT_DIR = Path("data/standard/semantic_json")

def list_pdfs(root: Path):
    return sorted(root.rglob("*.pdf"))

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for pdf in list_pdfs(RAW_PDF_DIR):
        print(f"[INFO] Semantic parsing: {pdf}")
        blocks_by_page = get_pdf_page_blocks(pdf.read_bytes(), filename=str(pdf))
        schema = build_semantic_schema(blocks_by_page)

        out = OUTPUT_DIR / pdf.with_suffix(".semantic.json").name
        with out.open("w", encoding="utf-8") as f:
            json.dump(schema, f, ensure_ascii=False, indent=2)

        print(f"[INFO] Wrote semantic JSON: {out}")

if __name__ == "__main__":
    main()
