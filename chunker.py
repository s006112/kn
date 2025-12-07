#!/usr/bin/env python3
"""
chunker.py

從 /data/raw/standard/ 掃描所有 PDF，使用 PyMuPDF 以最「原始」的方式抽取文字，
不做 OCR、不做版面清洗，只額外移除重複出現的下載水印文字，將全文輸出為 .txt。
"""

from __future__ import annotations

import sys
import re
from pathlib import Path

import fitz  # PyMuPDF


BASE_DIR = Path("data/raw/standard")


# --- 水印清洗 Helper（只針對明確的下載水印，不碰正文） ------------------------

WATERMARK_PATTERNS = [
    r"Document Was Downloaded By .*? SUPER X MFG LTD .*?\n",
    r"ULSE INC\. COPYRIGHTED MATERIAL .*?\n",
    r"REPRODUCTION OR DISTRIBUTION WITHOUT PERMISSION FROM ULSE INC\.\n?",
]

WATERMARK_REGEX = re.compile("|".join(WATERMARK_PATTERNS), flags=re.IGNORECASE)


def clean_watermark(text: str) -> str:
    """
    只移除固定格式的下載水印，不做任何其他內容處理。
    """
    return WATERMARK_REGEX.sub("", text)


# --- Raw PDF 抽取 ---------------------------------------------------------------

def extract_pdf_raw(pdf_path: Path) -> str:
    """
    使用 PyMuPDF 以最接近「原始內容流」的方式抽取文字：
    - 使用 page.get_text("raw")
    - 不做 strip、不重排
    - 不做 OCR 回退
    """
    text_parts: list[str] = []

    with fitz.open(pdf_path) as doc:
        for page in doc:
            page_text = page.get_text("raw") or ""
            text_parts.append(page_text)

    return "".join(text_parts)


# --- 主流程 ---------------------------------------------------------------------

def process_all_pdfs(base_dir: Path) -> None:
    if not base_dir.exists():
        print(f"[ERROR] Base directory not found: {base_dir}", file=sys.stderr)
        sys.exit(1)

    pdf_files = sorted(base_dir.rglob("*.pdf"))

    if not pdf_files:
        print(f"[INFO] No PDF files found under: {base_dir}")
        return

    for pdf_path in pdf_files:
        try:
            print(f"[INFO] Processing: {pdf_path}")

            raw_text = extract_pdf_raw(pdf_path)

            # ✅ 只在這一行做「水印級清洗」，不做任何正文整理
            cleaned_text = clean_watermark(raw_text)

            # ✅ 你已改成：XXXXX.txt
            out_path = pdf_path.with_suffix(".txt")

            out_path.write_text(cleaned_text, encoding="utf-8")

            print(f"[INFO] Wrote: {out_path}")

        except Exception as exc:
            print(f"[ERROR] Failed on {pdf_path}: {exc}", file=sys.stderr)


def main() -> None:
    process_all_pdfs(BASE_DIR)


if __name__ == "__main__":
    main()
