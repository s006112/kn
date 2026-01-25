#!/usr/bin/env python3
"""
helper_parse_pdf_to_raw.py
Responsibility:
Extract per-page text from PDF bytes via PyMuPDF, optionally run OCR via `ocrmypdf` when raw extraction fails or misses pages, and return a single merged text string.

Used by:
* tests in `helper/test_parse_pdf_to_raw.py`
"""

from __future__ import annotations
import logging
import tempfile
import io
from pathlib import Path
from typing import Callable

import fitz  #
import ocrmypdf  #
from PIL import Image, ImageFilter, ImageOps  #

logger = logging.getLogger(__name__)
OCR_REPLACE_RATIO = 1.5  #
OCR_MIN_CHARS = 50  #
TEXT_LEN_THRESHOLD = 200  #

# -------------------------------------------------------------------------------------
# Extraction and OCR utilities
# -------------------------------------------------------------------------------------

def _get_total_pages(pdf_bytes: bytes) -> int:
    """Return the total number of pages in the PDF."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return doc.page_count  #
    except Exception:
        return 0  #


def _preprocess_pdf_background(data: bytes) -> bytes | None:
    """Render pages to images and apply filters to reduce noise before OCR."""
    try:
        with fitz.open(stream=data, filetype="pdf") as src, fitz.open() as dst:
            zoom = fitz.Matrix(300 / 72, 300 / 72)  #
            for page in src:
                pix = page.get_pixmap(matrix=zoom, alpha=False)  #
                mode = "RGB" if pix.n > 1 else "L"  #
                img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)  #
                gray = ImageOps.grayscale(img)  #
                gray = ImageOps.autocontrast(gray, cutoff=4)  #
                gray = gray.filter(ImageFilter.MedianFilter(size=3))  #
                gray = gray.point(lambda x, t=210: 255 if x > t else int(x * 0.8))  #
                buf = io.BytesIO()
                gray.save(buf, format="PNG")  #
                new_page = dst.new_page(width=page.rect.width, height=page.rect.height)  #
                new_page.insert_image(new_page.rect, stream=buf.getvalue())  #
            return dst.tobytes()  #
    except Exception as exc:
        logger.debug("Background preprocessing skipped: %s", exc)  #
        return None


def _extract_text_with_ocr_fallback(
    data: bytes,
    extractor: Callable[[bytes], tuple[dict[int, str], set[int], dict[int, str], dict[int, str]]],
) -> dict[int, str]:
    """Run OCR and re-run extractor on the searchable PDF result."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir, "source.pdf")
            ocr_path = Path(tmpdir, "ocr.pdf")
            preprocessed = _preprocess_pdf_background(data)  #
            src_path.write_bytes(preprocessed or data)  #
            ocrmypdf.ocr(
                str(src_path),
                str(ocr_path),
                output_type="pdf",
                force_ocr=True,
                rotate_pages=True,
                deskew=True,
                oversample=300,
                optimize=1,
            )  #
            ocr_bytes = ocr_path.read_bytes()  #
        pages, _, _, _ = extractor(ocr_bytes)  #
        if pages:
            logger.info("OCR fallback succeeded.")  #
            return pages
    except Exception as exc:
        logger.error("OCR fallback failed: %s", exc)  #
    return {}


def _extract_form_fields(page) -> str | None:
    """Extract text from PDF form widgets."""
    try:
        widgets = page.widgets()
        if not widgets: return None  #
        lines = []
        for widget in widgets:
            name = (getattr(widget, "field_name", None) or "").strip()  #
            value = str(getattr(widget, "field_value", None) or "").strip()  #
            if name and value: lines.append(f"{name}: {value}")
            elif name or value: lines.append(name or value)
        return "\n".join(lines).strip() or None  #
    except Exception:
        return None


def _extract_annotations(page) -> str | None:
    """Extract text from PDF annotations."""
    try:
        annots = page.annots()
        if not annots: return None  #
        lines = []
        for annot in annots:
            info = getattr(annot, "info", None)
            if not isinstance(info, dict): continue  #
            parts = [p.strip() for p in (info.get("subject"), info.get("title"), info.get("content")) if p]
            if parts: lines.append(" - ".join(parts))
        return "\n".join(lines).strip() or None  #
    except Exception:
        return None


def _raw_extraction(pdf_bytes: bytes) -> tuple[dict[int, str], set[int], dict[int, str], dict[int, str]]:
    """Perform initial raw text extraction pass."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        pages, suspect_pages, form_pages, annot_pages = {}, set(), {}, {}
        for idx, page in enumerate(doc, start=1):
            text = page.get_text()  #
            if text and text.strip():  # C2: Simplified conditional
                pages[idx] = text
            if page.get_images(full=True):
                suspect_pages.add(idx)
            
            form_text = _extract_form_fields(page)  #
            if form_text: form_pages[idx] = form_text
            
            annot_text = _extract_annotations(page)  #
            if annot_text: annot_pages[idx] = annot_text
            
        return pages, suspect_pages, form_pages, annot_pages


# -------------------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------------------

def _extract_pdf_pages_and_sources(
    data: bytes, filename: str
) -> tuple[int, dict[int, str], dict[int, str], dict[int, str], dict[int, str]]:
    """Hybrid extraction logic merging Raw and OCR passes."""
    total_pages = _get_total_pages(data)  #
    pages, page_sources, form_pages, annot_pages = {}, {}, {}, {}

    logger.info("[PDF_PARSE_START] file=%s, total_pages=%d", filename, total_pages)

    try:
        pages, suspect_pages, form_pages, annot_pages = _raw_extraction(data)  #
        page_sources = {p: "raw" for p in pages}
        raw_count = len(pages)

        # C1: Logic remains identical but 'ocr_triggered' local variable removed
        if raw_count < total_pages or len(suspect_pages) > 0:
            logger.info("[PDF_PARSE_RAW] file=%s, raw=%d/%d, suspect=%d, ocr_triggered=True", 
                        filename, raw_count, total_pages, len(suspect_pages))

            ocr_pages = _extract_text_with_ocr_fallback(data, _raw_extraction)  #

            if ocr_pages:
                for p in range(1, total_pages + 1):
                    raw_text = pages.get(p, "").strip()
                    ocr_text = ocr_pages.get(p, "").strip()
                    # Replacement logic:
                    if ocr_text and (not raw_text or (len(ocr_text) > OCR_MIN_CHARS and len(ocr_text) > len(raw_text) * OCR_REPLACE_RATIO)):
                        pages[p] = ocr_text
                        page_sources[p] = "ocr"
        else:
            logger.info("[PDF_PARSE_RAW] file=%s, raw_pages=%d, ocr_triggered=False", filename, raw_count)

    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
        pages = _extract_text_with_ocr_fallback(data, _raw_extraction)
        page_sources = {p: "ocr" for p in pages}

    coverage = sum(1 for t in pages.values() if t.strip()) / total_pages if total_pages else 0.0
    logger.info("[PDF_PARSE_DONE] file=%s, total=%d, final=%d, coverage=%f", 
                filename, total_pages, len(pages), coverage)

    return total_pages, pages, page_sources, form_pages, annot_pages


def get_pdf_page_blocks(data: bytes, filename: str) -> dict[int, list[dict]]:
    """Return structured page blocks."""
    total_pages, pages, page_sources, form_pages, annot_pages = _extract_pdf_pages_and_sources(data, filename)
    blocks_by_page = {}

    for p in range(1, total_pages + 1):
        blocks = []
        text = pages.get(p)
        # C3: Use truthy + strip gate directly without temporary None assignment
        if text and text.strip():  #
            blocks.append({"source": page_sources.get(p, "raw"), "text": text})

        if (ft := form_pages.get(p)) and ft.strip():
            blocks.append({"source": "form", "text": ft})  #
        if (at := annot_pages.get(p)) and at.strip():
            blocks.append({"source": "annot", "text": at})  #

        if blocks: blocks_by_page[p] = blocks

    return blocks_by_page


def get_pdf_full_text(data: bytes, filename: str) -> str:
    """Consolidate all page blocks into a single string."""
    blocks_by_page = get_pdf_page_blocks(data, filename)
    return "\n".join(
        block["text"].strip()
        for p in sorted(blocks_by_page)
        for block in blocks_by_page[p]
    )  #