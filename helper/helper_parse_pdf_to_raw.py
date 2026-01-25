#!/usr/bin/env python3
"""
helper_parse_pdf_to_raw.py
Responsibility:
Extract per-page text from PDF bytes via PyMuPDF, optionally run OCR via Tesseract (page-level) when raw extraction is insufficient


Used by:
* helper/test_parse_pdf_to_raw.py
* core_per_report.py
* core_so_import.py
* rag/chunk_att.py
* rag/standard_pdf_to_txt.py
* tool/tool_pdf_parser.py

"""

from __future__ import annotations
import logging
import pytesseract
import fitz  #
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

def _ocr_page_with_tesseract(page, dpi: int = 300) -> str:
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    mode = "RGB" if pix.n > 1 else "L"
    img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)

    # 可选预处理（保留你已有那套）
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img, cutoff=4)
    img = img.filter(ImageFilter.MedianFilter(size=3))

    text = pytesseract.image_to_string(
        img,
        lang="eng+chi_sim+chi_tra",
        config="--psm 6"
    )
    return text.strip()

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


def _extract_text_with_ocr_fallback(
    pages: dict[int, str],
    page_sources: dict[int, str],
    suspect_pages: set[int],
    doc,
) -> tuple[dict[int, str], dict[int, str]]:
    for idx, page in enumerate(doc, start=1):
        raw_text = pages.get(idx, "").strip()

        need_ocr = (
            not raw_text or
            len(raw_text) < OCR_MIN_CHARS or
            idx in suspect_pages
        )

        if need_ocr:
            try:
                ocr_text = _ocr_page_with_tesseract(page)
            except Exception as e:
                logger.warning("OCR failed on page %d: %s", idx, e)
                continue

            if ocr_text and (
                not raw_text or
                (len(ocr_text) > OCR_MIN_CHARS and len(ocr_text) > len(raw_text) * OCR_REPLACE_RATIO)
            ):
                pages[idx] = ocr_text
                page_sources[idx] = "ocr"

    return pages, page_sources


# -------------------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------------------

def _extract_pdf_pages_and_sources( data: bytes, filename: str ) -> tuple[int, dict[int, str], dict[int, str], dict[int, str], dict[int, str]]:
    total_pages = _get_total_pages(data)
    pages, suspect_pages, form_pages, annot_pages = _raw_extraction(data)
    page_sources = {p: "raw" for p in pages}

    with fitz.open(stream=data, filetype="pdf") as doc:
        pages, page_sources = _extract_text_with_ocr_fallback(pages, page_sources, suspect_pages, doc)

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

    form_blocks = sum(
        1 for blocks in blocks_by_page.values() for block in blocks if block["source"] == "form"
    )
    annot_blocks = sum(
        1 for blocks in blocks_by_page.values() for block in blocks if block["source"] == "annot"
    )
    logger.info(
        "[PDF_PARSE_BLOCKS] file=%s, form_blocks=%d, annot_blocks=%d",
        filename,
        form_blocks,
        annot_blocks,
    )

    return blocks_by_page


def get_pdf_full_text(data: bytes, filename: str) -> str:
    """Consolidate all page blocks into a single string."""
    blocks_by_page = get_pdf_page_blocks(data, filename)
    return "\n".join(
        block["text"].strip()
        for p in sorted(blocks_by_page)
        for block in blocks_by_page[p]
    )  #
