#!/usr/bin/env python3
"""
Responsibility:
PDF extraction helpers for the standard-document pipeline: extract per-page text from PDFs using PyMuPDF, optionally run OCR, and produce either raw text or fixed-size chunk tasks with metadata.

Used by:
* core_per_report.py
* core_so_import.py
* rag/std_01_pdf_to_txt.py
* rag/chunk_att.py

Pipelines:
- open_pdf -> extract_text -> ocr_fallback -> merge_pages -> chunk_fixed

Invariants:
- `extract_text_from_pdf_bytes` returns a `{page_number: text}` mapping with 1-based page numbers when extraction succeeds.
- OCR fallback writes temporary files and re-extracts text from the OCR output PDF.

Out of scope:
- Email attachment routing and processing (handled elsewhere).
- Embedding/indexing/retrieval.
"""

from __future__ import annotations
import inspect
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
# 輔助工具函數：從呼叫堆疊中自動推測 filename（若未在參數中顯式提供）
# -------------------------------------------------------------------------------------
def _infer_filename_from_stack() -> str | None:
    """
    Purpose:
    Infer a filename from the call stack by looking for a local variable named `fn`.

    Inputs:
    - None.

    Outputs:
    - Filename string when found, else `None`.

    Side effects:
    - Inspects the Python call stack.

    Failure modes:
    - None.
    """

    for frame in inspect.stack()[1:]:
        fn = frame.frame.f_locals.get("fn")
        if isinstance(fn, str):
            return fn
    return None

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
    - New PDF bytes containing rasterized pages, or `None` when preprocessing is skipped/fails.

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

def extract_text_with_pymupdf(data: bytes) -> dict[int, str]:
    """
    Purpose:
    Extract per-page text from PDF bytes using PyMuPDF, with OCR fallback when extraction yields no text or fails.

    Inputs:
    - data: Raw PDF bytes.

    Outputs:
    - Mapping of 1-based page index to extracted text.

    Side effects:
    - Logs extraction and fallback progress.

    Failure modes:
    - Returns `{}` when both direct extraction and OCR fallback produce no text.
    """

    def _run_extraction(pdf_bytes: bytes) -> dict[int, str]:
        """
        Purpose:
        Run a single PyMuPDF extraction pass on PDF bytes.

        Inputs:
        - pdf_bytes: Raw PDF bytes.

        Outputs:
        - Mapping of 1-based page index to extracted text (no OCR).

        Side effects:
        - Opens a PyMuPDF document handle.

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

    try:
        pages = _run_extraction(data)
        if pages:
            return pages
        # PyMuPDF returned no text; fall back to OCR once.
        logger.info("PyMuPDF extracted no text, attempting OCR fallback.")
    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
    return _extract_text_with_ocr_fallback(data, _run_extraction)

# -------------------------------------------------------------------------------------
# 封裝提取流程：對外公開的頁面文字提取接口
# -------------------------------------------------------------------------------------
def extract_text_from_pdf_bytes(data: bytes, filename: str | None = None) -> dict[int, str]:
    """
    Purpose:
    Extract per-page text from PDF bytes and log the page count, optionally labeling logs with a filename.

    Direct used by:
    * core_so_import.py
    * core_per_report.py
    * rag/std_01_pdf_to_txt.py

    Inputs:
    - data: Raw PDF bytes.
    - filename: Optional filename used for logging; when omitted, `_infer_filename_from_stack()` is attempted.

    Outputs:
    - Mapping of 1-based page index to extracted text.

    Side effects:
    - Inspects the call stack when `filename` is not provided.
    - Logs extraction progress.

    Failure modes:
    - Returns `{}` when extraction yields no text.
    """

    filename = filename or _infer_filename_from_stack()  # 若未提供 filename，則自動嘗試推測
    pages = extract_text_with_pymupdf(data)  # 調用實際的 PDF 擷取器
    ctx = f" ({filename})" if filename else ""
    logger.info("Extraction complete: %d pages%s", len(pages), ctx)
    return pages  # 回傳每頁清洗後文字的字典

# -------------------------------------------------------------------------------------
# 高階共用函數：回傳合併後的完整 PDF 文字（不含分頁結構）
# 供 rag/chunk_att.py 使用，用於將整份 PDF 匯出為 .txt 檔案
# -------------------------------------------------------------------------------------
def get_pdf_full_text(data: bytes, filename: str | None = None) -> str:
    """
    Purpose:
    Merge extracted per-page PDF text into a single string.

    Inputs:
    - data: Raw PDF bytes.
    - filename: Optional filename forwarded to `extract_text_from_pdf_bytes`.

    Outputs:
    - Combined text with pages joined by blank lines.

    Side effects:
    - Calls `extract_text_from_pdf_bytes` (which may run OCR fallback and log).

    Failure modes:
    - Returns an empty string when no pages contain non-whitespace text.
    """

    pages = extract_text_from_pdf_bytes(data, filename)  # 提取每頁文字
    return "\n\n".join(
        text for _, text in sorted(pages.items())  # 依頁碼排序並合併
        if text.strip()
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

    Used by:
    * rag/chunk_att.py

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
