#!/usr/bin/env python3
"""
Responsibility:
Extract per-page text from PDF bytes via PyMuPDF, optionally run OCR via `ocrmypdf` when raw extraction fails or misses pages, and return a single merged text string.

Used by:
* tests in `helper/test_parse_pdf_to_raw.py`

Pipelines:
- pdf_bytes -> raw_extract -> ocr_extract -> merge_pages -> full_text

Invariants:
- Page numbering in per-page mappings is 1-based.
- OCR fallback is attempted only when the initial raw pass errors or yields fewer extracted pages than the PDF total.
- OCR results are merged only when OCR produced at least one page.
- Per-page OCR replacement requires non-empty OCR text and either an empty raw page or a significant OCR length advantage.
- Returned full text joins final page texts in ascending page index with newline separators.

Out of scope:
- Upload validation and routing.
- Layout reconstruction and semantic chunking.
- Indexing, embedding, or retrieval.

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
OCR_REPLACE_RATIO = 1.5
OCR_MIN_CHARS = 50

# -------------------------------------------------------------------------------------
# Keep low-level extraction and OCR utilities separate so the same extractor can be reused before and after OCR.
# -------------------------------------------------------------------------------------
def _get_total_pages(pdf_bytes: bytes) -> int:
    """
    Purpose:
    Return the total number of pages in the PDF.

    Inputs:
    - pdf_bytes: Raw PDF bytes.

    Outputs:
    - Total page count, or 0 when the PDF cannot be opened.

    Side effects:
    - None.

    Failure modes:
    - Returns 0 on any exception from PyMuPDF.
    """
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return doc.page_count
    except Exception:
        return 0


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
    Purpose:
    Extract and merge per-page text from a PDF payload using a two-pass approach:
    1. PyMuPDF text extraction.
    2. OCR extraction when raw extraction errors or misses pages, then merge OCR into the raw result.

    Inputs:
    - data: Raw PDF bytes.
    - filename: Filename used for logging.

    Outputs:
    - Combined text with page texts joined by newlines (ascending page index).

    Side effects:
    - Logs extraction progress, OCR triggering, and coverage metrics.
    - May create temporary files and run OCR via `ocrmypdf` when fallback triggers.
    - Internally tracks per-page source decisions ("raw" or "ocr") for pages chosen during merging.

    Failure modes:
    - Returns an empty string when no pages contain non-whitespace text.
    """
    total_pages = _get_total_pages(data)
    ocr_triggered = False
    pages: dict[int, str] = {}
    page_sources: dict[int, str] = {}

    logger.info("[PDF_PARSE_START] file=%s, total_pages=%d", filename, total_pages)

    try:
        # Step 0/1: Initial Raw Extraction
        pages = _raw_extraction(data)
        page_sources = {p: "raw" for p in pages}
        raw_count = len(pages)

        # Step 1: Hybrid Trigger - OCR if pages are missing
        if raw_count < total_pages:
            ocr_triggered = True
            logger.info(
                "[PDF_PARSE_RAW] file=%s, raw_pages=%d/%d, ocr_triggered=True",
                filename, raw_count, total_pages
            )
            logger.info("Partial extraction detected. Running OCR to recover missing pages...")
            
            ocr_pages = _extract_text_with_ocr_fallback(data, _raw_extraction)

            if not ocr_pages:
                logger.warning(
                    "OCR produced no usable pages, keeping raw extraction only."
                )
            else:
                # Step 1: Merge Strategy - Fill gaps in raw_pages with OCR content
                for p in range(1, total_pages + 1):
                    raw_text = pages.get(p, "").strip()
                    ocr_text = ocr_pages.get(p, "").strip()

                    use_ocr = False
                    if ocr_text and (
                        not raw_text
                        or (
                            len(ocr_text) > OCR_MIN_CHARS
                            and len(ocr_text) > len(raw_text) * OCR_REPLACE_RATIO
                        )
                    ):
                        use_ocr = True

                    if use_ocr:
                        pages[p] = ocr_text
                        page_sources[p] = "ocr"
                    elif raw_text:
                        page_sources.setdefault(p, "raw")
        else:
            logger.info(
                "[PDF_PARSE_RAW] file=%s, raw_pages=%d, ocr_triggered=False",
                filename, raw_count
            )

    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
        ocr_triggered = True
        logger.info(
            "[PDF_PARSE_RAW] file=%s, raw_pages=0, ocr_triggered=True",
            filename
        )
        # Full fallback on critical error
        pages = _extract_text_with_ocr_fallback(data, _raw_extraction)
        page_sources = {p: "ocr" for p in pages}

    final_pages_count = len(pages)
    covered_pages = sum(1 for t in pages.values() if t.strip())
    coverage = covered_pages / total_pages
    
    logger.info(
        "Extraction complete: %d pages, %d covered (%s)",
        final_pages_count,
        covered_pages,
        filename,
    )

    logger.info(
        "[PDF_PARSE_DONE] file=%s, total_pages=%d, final_pages=%d, covered_pages=%d, coverage=%f",
        filename,
        total_pages,
        final_pages_count,
        covered_pages,
        coverage,
    )
    return "\n".join(
        text.strip() for _, text in sorted(pages.items())
    )
