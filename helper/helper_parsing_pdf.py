#!/usr/bin/env python3
"""
Responsibility:
PDF parsing helpers that extract per-page text with PyMuPDF, optionally run OCR, and optionally emit fixed-size chunks with metadata.

Used by:
* core_per_report.py
* core_so_import.py
* rag/chunk_att.py
* rag/standard_pdf_to_txt.py
* tool/tool_pdf_parser.py

Pipelines:
- pdf_bytes -> text_pages -> ocr_fallback -> merged_text -> chunk_fixed

Invariants:
- `get_pdf_full_text` returns a merged string (page texts joined by newlines) when extraction succeeds.
- OCR fallback writes temporary files and re-extracts text from the OCR output PDF.

Out of scope:
- Upload validation and file routing.
- Embedding, indexing, or retrieval.
"""

from __future__ import annotations
import logging
import tempfile
import io
from pathlib import Path
from typing import Callable, List, Tuple

import fitz  # PyMuPDF：用於處理 PDF 文件的主要函式庫
import ocrmypdf  # OCR fallback for image-based PDFs
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

PDF_EXTS = {".pdf"}


# -------------------------------------------------------------------------------------
# 核心 PDF 解析器（使用 PyMuPDF）
# -------------------------------------------------------------------------------------
def _preprocess_pdf_background(data: bytes) -> bytes | None:
    """
    Purpose:
    Render PDF pages and apply simple image filters to reduce background noise before OCR.

    Inputs:
    - data: Raw PDF bytes.

    Outputs:
    - New PDF bytes containing rasterized pages, or `None` when preprocessing is skipped or fails.

    Side effects:
    - Renders pages via PyMuPDF and creates in-memory images.
    - Logs debug messages when preprocessing cannot be applied.

    Failure modes:
    - Returns `None` on any exception.
    """

    try:
        with fitz.open(stream=data, filetype="pdf") as src, fitz.open() as dst:
            zoom = fitz.Matrix(300 / 72, 300 / 72)
            for page in src:
                pix = page.get_pixmap(matrix=zoom, alpha=False)
                mode = "RGB" if pix.n > 1 else "L"
                img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
                gray = ImageOps.grayscale(img)
                gray = ImageOps.autocontrast(gray, cutoff=4)
                gray = gray.filter(ImageFilter.MedianFilter(size=3))
                gray = gray.point(lambda x, t=210: 255 if x > t else int(x * 0.8))
                buf = io.BytesIO()
                gray.save(buf, format="PNG")
                new_page = dst.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(new_page.rect, stream=buf.getvalue())
            return dst.tobytes()
    except Exception as exc:
        logger.debug("Background preprocessing skipped: %s", exc)
        return None


def _extract_text_with_ocr_fallback(
    data: bytes,
    extractor: Callable[[bytes], dict[int, str]],
) -> dict[int, str]:
    """
    Purpose:
    Run OCR to produce a searchable PDF and then re-run a provided extractor on the OCR output.

    Inputs:
    - data: Raw PDF bytes.
    - extractor: Callable that extracts per-page text from PDF bytes.

    Outputs:
    - Mapping of 1-based page index to extracted text.

    Side effects:
    - Creates temporary files/directories.
    - Runs `ocrmypdf.ocr` and reads its output file.
    - Logs OCR progress and failures.

    Failure modes:
    - Returns `{}` on OCR failure or when the extractor returns no pages.
    """

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir, "source.pdf")
            ocr_path = Path(tmpdir, "ocr.pdf")
            preprocessed = _preprocess_pdf_background(data)
            src_path.write_bytes(preprocessed or data)
            # Run OCR to produce a searchable PDF; force OCR to avoid Ghostscript regression with skip_text.
            ocrmypdf.ocr(
                str(src_path),
                str(ocr_path),
                output_type="pdf",
                force_ocr=True,
                rotate_pages=True,
                deskew=True,
                oversample=300,
                optimize=1,
            )
            ocr_bytes = ocr_path.read_bytes()
        pages = extractor(ocr_bytes)
        if pages:
            logger.info("OCR fallback succeeded.")
            return pages
        logger.warning("OCR fallback produced no text.")
    except Exception as exc:
        logger.error("OCR fallback failed: %s", exc)
    return {}


def _raw_extraction(pdf_bytes: bytes) -> dict[int, str]:
    """
    Purpose:
    Perform a single text-extraction pass over PDF bytes using PyMuPDF without OCR.

    Inputs:
    - pdf_bytes: Raw PDF bytes.

    Outputs:
    - Mapping of 1-based page index to extracted text (whitespace-only pages omitted).

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions raised by PyMuPDF operations.
    """

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        pages: dict[int, str] = {}
        for idx, page in enumerate(doc, start=1):
            text = page.get_text()
            if text and text.strip():
                pages[idx] = text
        return pages


# -------------------------------------------------------------------------------------
# 高階共用函數：回傳合併後的完整 PDF 文字（不含分頁結構）
# 供 rag/chunk_att.py 使用，用於將整份 PDF 匯出為 .txt 檔案
# -------------------------------------------------------------------------------------
def get_pdf_full_text(data: bytes, filename: str) -> str:
    """
    Purpose:
    Extract full text from a PDF payload, using PyMuPDF with an OCR fallback, then merge pages into one string.

    Inputs:
    - data: Raw PDF bytes.
    - filename: Filename used for logging.

    Outputs:
    - Combined text with pages joined by newlines.

    Side effects:
    - Logs extraction, fallback progress, and extraction summary.

    Failure modes:
    - Returns an empty string when no pages contain non-whitespace text.
    """

    try:
        pages = _raw_extraction(data)
        if not pages:
            logger.info("PyMuPDF extracted no text, attempting OCR fallback.")
            pages = _extract_text_with_ocr_fallback(data, _raw_extraction)
    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
        pages = _extract_text_with_ocr_fallback(data, _raw_extraction)

    logger.info("Extraction complete: %d pages (%s)", len(pages), filename)
    return "\n".join(
        text.strip() for _, text in sorted(pages.items())  # 依頁碼排序並合併
        if text and text.strip()
    )


def extract_pdf_attachment_tasks(
    data: bytes,
    filename: str,
    base_meta: dict,
    max_len: int,
) -> List[Tuple[str, dict]]:
    """
    Purpose:
    Produce fixed-size `(chunk_text, metadata)` tuples for a PDF payload.

    Inputs:
    - data: PDF bytes.
    - filename: Filename used for metadata fields.
    - base_meta: Base metadata to merge into each chunk metadata dict.
    - max_len: Fixed chunk size in characters.

    Outputs:
    - List of `(chunk_text, metadata)` tuples with 1-based `seq`.

    Side effects:
    - Calls `get_pdf_full_text` (which may run OCR fallback and log).

    Failure modes:
    - Returns `[]` when no non-empty chunks are produced.
    """

    full_text = get_pdf_full_text(data, filename)  # 拿到整份 PDF 的合併文字

    chunks = [
        full_text[i:i + max_len]
        for i in range(0, len(full_text), max_len)
    ]  # 將全文以 max_len 字符為單位切割（固定長度，無語義分析）

    return [
        (
            chunk,
            {
                **base_meta,  # 合併外部提供的 metadata
                "part": "attachment",  # 標記此為附件來源
                "file_type": "pdf",
                "attachment": filename,
                "seq": i + 1,  # chunk 序號（從 1 開始）
            },
        )
        for i, chunk in enumerate(chunks)
        if chunk.strip()
    ]
