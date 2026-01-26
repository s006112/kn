#!/usr/bin/env python3
"""
standard_pdf_to_semantic_jsonl.py
Convert standard PDF files to canonical JSONL format.
Also write per-file JSONL for debugging / audit.
"""

from pathlib import Path
import json
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_parse_pdf_to_raw import get_pdf_page_blocks

RAW_PDF_DIR = Path("data/standard/pdf")
PER_FILE_DIR = Path("data/standard/jsonl")  # 分支：每个文件一份
CANONICAL_JSONL = Path("data/canonical_blocks.jsonl")   # 主干：所有 block 汇总

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
            blocks_by_page = get_pdf_page_blocks(pdf.read_bytes(), filename=str(pdf))

            seq = 0
            with per_file_path.open("w", encoding="utf-8") as per_file_out:
                for page in sorted(blocks_by_page.keys()):
                    for block in blocks_by_page[page]:
                        text = block["text"].strip()
                        if not text:
                            continue

                        seq += 1

                        row = {
                            "doc_id": doc_id,
                            "block_id": f"{doc_id}_b{seq:05d}",
                            "page": page,
                            "source": block["source"],

                            "part": "document",
                            "file_type": "pdf",
                            "attachment": None,

                            "page": seq,
                            "char": len(text),
                            "word": len(text.split()),
                            "text": text,
                        }

                        line = json.dumps(row, ensure_ascii=False) + "\n"

                        # 写入主 canonical
                        canonical_out.write(line)

                        # 写入 per-file
                        per_file_out.write(line)

            print(f"[INFO] Finished {pdf}, blocks={seq}")
            print(f"[INFO] Per-file JSONL: {per_file_path}")

    print(f"[DONE] Canonical JSONL written to {CANONICAL_JSONL}")


if __name__ == "__main__":
    main()
