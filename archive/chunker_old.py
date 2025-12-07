#!/usr/bin/env python3
"""
chunker.py

從 /data/raw/standard/ 掃描所有 PDF，使用 PyMuPDF 以最「原始」的方式抽取文字，
不做 OCR、不做清洗、不手動插入額外字符，將全文合併為單一字串，輸出為 .txt 檔。
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF


# 根目錄：可以按需改成參數或環境變數
BASE_DIR = Path("data/raw/standard")


def extract_pdf_raw(pdf_path: Path) -> str:
    """
    使用 PyMuPDF 以最接近「原始內容流」的方式抽取文字：
    - 使用 page.get_text("raw")
    - 不做任何後處理（不 strip、不重排、不手工加換行）
    - 不做 OCR 回退

    返回整份 PDF 的文字（所有頁的 raw text 直接拼接，頁與頁之間不額外插入字符）。
    """
    text_parts: list[str] = []

    # 注意：這裏不捕獲異常，讓上層決定如何處理錯誤
    with fitz.open(pdf_path) as doc:
        for page in doc:
            # "raw" 模式：盡量按內容流順序輸出文字，避免版面重排
            page_text = page.get_text("raw")
            if page_text is None:
                page_text = ""
            # 不做 strip、不加額外換行
            text_parts.append(page_text)

    # 頁與頁之間也不插入任何額外分隔符
    return "".join(text_parts)


def process_all_pdfs(base_dir: Path) -> None:
    """
    掃描目錄下所有 .pdf 檔（含子目錄），對每一個產生對應的 .txt 檔：
    - input:  /data/raw/standard/XXX.pdf
    - output: /data/raw/standard/XXX.pdf.txt
    """
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

            # 輸出檔名：原檔名 + ".txt"（例如 foo.pdf → foo.pdf.txt）
            out_path = pdf_path.with_suffix(pdf_path.suffix + ".txt")

            out_path.write_text(raw_text, encoding="utf-8")
            print(f"[INFO] Wrote: {out_path}")
        except Exception as exc:
            print(f"[ERROR] Failed on {pdf_path}: {exc}", file=sys.stderr)


def main() -> None:
    process_all_pdfs(BASE_DIR)


if __name__ == "__main__":
    main()
