#!/usr/bin/env python3
"""
Responsibility:
Extracts and sanitizes text from PDF bytes using PyMuPDF, with an OCR fallback for image-based PDFs, and converts the result into fixed-size attachment chunk tasks.

Used by:
* archive/XX_dvt.py

Pipelines:
- extract_pymupdf -> sanitize_text -> ocr_fallback -> merge_pages -> chunk_fixed

Invariants:
- Page text is returned as a `{page_number: sanitized_text}` mapping with 1-based page numbers when extraction succeeds.
- If PyMuPDF yields no text or raises, OCR is attempted once via `ocrmypdf.ocr(skip_text=True)`.
- `OCR_LANGUAGES` controls OCR language selection via the `OCR_LANGUAGES` environment variable.

Out of scope:
- Email attachment iteration and routing (handled by `chunk_att`).
- JSONL persistence (handled by `chunk_json.JsonlWriter`).
"""

from __future__ import annotations
import os
import inspect
import logging
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

import fitz  # PyMuPDF：用於處理 PDF 文件的主要函式庫
import ocrmypdf  # OCR fallback for image-based PDFs
from chunk_att import build_attachment_tasks, join_nonempty_segments
from helper.helper_sanitize import sanitize_text  # 自訂的文字清洗函數，用來淨化提取出的 PDF 文字

logger = logging.getLogger(__name__)  # 初始化日誌記錄器

PDF_EXTS = {".pdf"}  # 支援的副檔名（目前僅限 PDF）

# -------------------------------------------------------------------------------------
# 輔助工具函數：從呼叫堆疊中自動推測 filename（若未在參數中顯式提供）
# -------------------------------------------------------------------------------------
def _infer_filename_from_stack() -> str | None:
    """
    Purpose:
    Infer a filename from the current call stack by looking for a local variable named `fn`.

    Inputs:
    - None.

    Outputs:
    - The inferred filename string, or `None` when not found.

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
def _extract_text_with_pymupdf(data: bytes) -> dict[int, str]:
    """
    Purpose:
    Extract and sanitize text from PDF bytes using PyMuPDF, with OCR fallback when no text is returned or extraction fails.

    Inputs:
    - data: Raw PDF file bytes.

    Outputs:
    - Mapping of 1-based page number to sanitized page text.

    Side effects:
    - Emits log messages about extraction outcomes and fallback usage.

    Failure modes:
    - Returns `{}` when both PyMuPDF extraction and OCR fallback fail to produce text.
    """

    def _run_extraction(pdf_bytes: bytes) -> dict[int, str]:
        """
        Purpose:
        Run a single PyMuPDF extraction pass over PDF bytes and sanitize per-page text.

        Inputs:
        - pdf_bytes: Raw PDF bytes to parse.

        Outputs:
        - Mapping of 1-based page number to sanitized page text.

        Side effects:
        - Opens a PyMuPDF document handle.

        Failure modes:
        - Propagates exceptions from PyMuPDF operations.
        """

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return {
                i: sanitize_text(t)
                for i, t in (
                    (n, p.get_text("text", sort=True).strip())
                    for n, p in enumerate(doc, 1)
                )
                if t
            }

    try:
        pages = _run_extraction(data)
        if pages:
            return pages
        # PyMuPDF returned no text; fall back to OCR once.
        logger.info("PyMuPDF extracted no text, attempting OCR fallback.")
    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
    return _extract_text_with_ocr_fallback(data, _run_extraction)


_OCR_LANGUAGES = os.environ.get("OCR_LANGUAGES", "chi_sim+eng")


def _extract_text_with_ocr_fallback(
    data: bytes,
    extractor: Callable[[bytes], dict[int, str]],
) -> dict[int, str]:
    """
    Purpose:
    Run OCR to produce a searchable PDF and then re-run a provided extractor on the OCR output.

    Inputs:
    - data: Raw PDF bytes to OCR.
    - extractor: Callable that extracts `{page_number: text}` from PDF bytes.

    Outputs:
    - Mapping of 1-based page number to sanitized page text.

    Side effects:
    - Creates temporary files/directories.
    - Runs `ocrmypdf.ocr` as an OCR step.
    - Emits log messages about OCR outcomes.

    Failure modes:
    - Returns `{}` on any OCR failure or when extraction yields no text.
    """

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir, "source.pdf")
            ocr_path = Path(tmpdir, "ocr.pdf")
            src_path.write_bytes(data)
            # Run OCR to produce a searchable PDF; skip existing text to avoid duplicates.
            ocrmypdf.ocr(
                str(src_path),
                str(ocr_path),
                skip_text=True,
                language=_OCR_LANGUAGES,
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

# -------------------------------------------------------------------------------------
# 封裝提取流程：對外公開的頁面文字提取接口
# -------------------------------------------------------------------------------------
def extract_text_from_pdf_bytes(data: bytes, filename: str | None = None) -> dict[int, str]:
    """
    Purpose:
    Extract per-page text from PDF bytes and log the result, optionally annotating logs with a filename.

    Inputs:
    - data: Raw PDF bytes.
    - filename: Optional filename used for logging; when omitted, `_infer_filename_from_stack()` is attempted.

    Outputs:
    - Mapping of 1-based page number to sanitized page text.

    Side effects:
    - Inspects the call stack when `filename` is not provided.
    - Emits log messages describing extraction results.

    Failure modes:
    - Returns `{}` when extraction yields no text.
    """

    filename = filename or _infer_filename_from_stack()  # 若未提供 filename，則自動嘗試推測
    pages = _extract_text_with_pymupdf(data)  # 調用實際的 PDF 擷取器
    ctx = f" ({filename})" if filename else ""
    logger.info("Extraction complete: %d pages%s", len(pages), ctx)
    return pages  # 回傳每頁清洗後文字的字典

# -------------------------------------------------------------------------------------
# PDF text aggregation helpers shared by downstream processing steps
# -------------------------------------------------------------------------------------
def get_pdf_full_text(data: bytes, filename: str | None = None) -> str:
    """
    Purpose:
    Aggregate extracted per-page PDF text into a single string.

    Inputs:
    - data: Raw PDF bytes.
    - filename: Optional filename forwarded to `extract_text_from_pdf_bytes`.

    Outputs:
    - A single string containing all non-empty pages joined by blank lines.

    Side effects:
    - Calls `extract_text_from_pdf_bytes` (and therefore may log and run OCR fallback).

    Failure modes:
    - Returns an empty string when no text is extracted.
    """

    return join_nonempty_segments(
        text for _, text in sorted(extract_text_from_pdf_bytes(data, filename).items())
    )

# -------------------------------------------------------------------------------------
# 結構化 chunk 任務輸出（供 01_email.py 等模組使用）
# -------------------------------------------------------------------------------------
def extract_pdf_attachment_tasks(
    data: bytes,
    filename: str,
    base_meta: dict,
    max_len: int,
) -> List[Tuple[str, dict]]:
    """
    Purpose:
    Produce fixed-size `(chunk_text, metadata)` tuples for a single PDF attachment.

    Inputs:
    - data: Attachment bytes.
    - filename: Attachment filename used for logging and metadata.
    - base_meta: Base metadata applied to all generated chunks.
    - max_len: Maximum chunk length in characters.

    Outputs:
    - List of `(chunk_text, metadata)` tuples; returns `[]` when extraction yields no text.

    Side effects:
    - Calls PDF extraction (which may run OCR fallback and log progress).

    Failure modes:
    - Returns `[]` when no non-empty chunks are produced.
    """

    return build_attachment_tasks(
        get_pdf_full_text(data, filename),
        base_meta=base_meta,
        file_type="pdf",
        filename=filename,
        max_len=max_len,
    )
