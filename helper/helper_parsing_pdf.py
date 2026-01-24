#!/usr/bin/env python3
"""
Responsibility:
PDF parsing helpers that extract per-page text with PyMuPDF and, when no text is recoverable (or extraction errors), run OCR via `ocrmypdf` to generate a searchable PDF and re-extract text.

Used by:
* core_per_report.py
* core_so_import.py
* rag/chunk_att.py
* rag/standard_pdf_to_txt.py
* tool/tool_pdf_parser.py

Pipelines:
- pdf_bytes -> text_extract -> ocr_pdf -> text_extract -> merge_pages

Invariants:
- Page numbering in per-page mappings is 1-based.
- OCR fallback is attempted only when the initial PyMuPDF pass yields no non-whitespace pages or raises an exception.
- `get_pdf_full_text` merges extracted pages by ascending page index with newline separators.

Out of scope:
- Upload validation and file routing.
- Embedding, indexing, or retrieval.
- Semantic chunking or layout reconstruction.

Improvements:
PDF parsing steps:
 ├─ Raw text stream       → PyMuPDF
 ├─ Image bitmap region   → OCR
 ├─ Vector region         → rasterize → OCR
 ├─ Form fields           → extract
 ├─ Annotations           → extract
 └─ Merge all into unified page text

"""

from __future__ import annotations
import logging
import tempfile
import io
from pathlib import Path
from typing import Callable

import fitz  # Used for both text extraction and page rasterization in the OCR path.
import ocrmypdf  # OCR fallback for image-based PDFs
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------------------
# Keep low-level extraction and OCR utilities separate so the same extractor can be reused before and after OCR.
# -------------------------------------------------------------------------------------
def _preprocess_pdf_background(data: bytes) -> bytes | None:
    """
    Purpose:
    Render pages to images and apply simple filters to reduce background noise before OCR.

    Inputs:
    - data: Raw PDF bytes.

    Outputs:
    - New PDF bytes containing rasterized pages, or `None` when preprocessing is skipped or fails.

    Side effects:
    - Renders pages via PyMuPDF and creates in-memory images.
    - Logs debug messages when preprocessing cannot be applied.

    Failure modes:
    - Returns `None` on any exception (caller falls back to the original PDF bytes).
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
    - Runs `ocrmypdf.ocr` and reads its output file (configured with `force_ocr=True`, rotation, deskew, `oversample=300`, and `optimize=1`).
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
    - Raises exceptions from PyMuPDF operations (callers decide whether to fall back to OCR).
    """

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        pages: dict[int, str] = {}
        for idx, page in enumerate(doc, start=1):
            text = page.get_text()
            if text and text.strip():
                pages[idx] = text
        return pages


# -------------------------------------------------------------------------------------
# Keep public helpers small and stable: downstream callers depend on a single merged string or fixed-size chunks.
# -------------------------------------------------------------------------------------
def get_pdf_full_text(data: bytes, filename: str) -> str:
    """
    Directly called by:
    * rag/standard_pdf_to_txt.py
    * tool/tool_pdf_parser.py

    Purpose:
    Extract full text from a PDF payload using a two-pass pipeline: PyMuPDF text extraction first, then OCR fallback when the first pass yields no non-whitespace text (or errors), then merge extracted pages into one string.

    Inputs:
    - data: Raw PDF bytes.
    - filename: Filename used for logging.

    Outputs:
    - Combined text with pages joined by newlines.

    Side effects:
    - Logs extraction, OCR fallback progress, and extraction summary.
    - May create temporary files and run OCR when fallback triggers.

    Failure modes:
    - Returns an empty string when no pages contain non-whitespace text.
    - For PDFs where some pages have extractable text and other pages are image-only, OCR fallback is not invoked and image-only pages remain missing from the output.
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
        text.strip() for _, text in sorted(pages.items())
    )
