#!/usr/bin/env python3
"""
helper_parse_pdf_to_raw.py
Responsibility:
Extract per-page text from PDF bytes via PyMuPDF, optionally run OCR via Tesseract (page-level) when raw extraction is insufficient


Used by:
* core_per_report.py
* core_so_import.py
* tool/tool_pdf_parser.py
* rag/parse_raw_to_jsonl.py

"""

from __future__ import annotations
import logging, sys
import pytesseract
import fitz  #
from PIL import Image, ImageFilter, ImageOps  #
from pathlib import Path

OCR_REPLACE_RATIO = 1.5  #
OCR_MIN_CHARS = 50  #
TEXT_LEN_THRESHOLD = 100  # suspect 页的最小有效文本阈值

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[1]
HELPER_DIR = Path(__file__).resolve().parent
for _p in (str(ROOT_DIR), str(HELPER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LOG_FILE = Path("data/parse_pdf_to_raw.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ----------------------------------------------------------------------
# Logging configuration
# ----------------------------------------------------------------------

if not logger.handlers:  # 防止多次 import 重复加 handler
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

fitz.TOOLS.mupdf_display_errors(False)

# ----------------------------------------------------------------------
# Helpers of PDF text extraction with OCR fallback
# ----------------------------------------------------------------------

def _unlock_doc_if_needed(doc, filename: str) -> bool:
    """
    Best-effort unlock for encrypted PDFs.

    Returns:
        True if the document is readable without a password (or was unlocked with
        an empty password). False when the document remains locked.
    """
    needs_pass = bool(getattr(doc, "needs_pass", False))
    if not needs_pass:
        return True

    authenticate = getattr(doc, "authenticate", None)
    if callable(authenticate):
        try:
            authenticate("")
        except Exception:
            pass

    if bool(getattr(doc, "needs_pass", False)):
        logger.warning("[PDF_ENCRYPTED] file=%s (skipping)", filename)
        return False

    return True


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


def _extract_visual_text_blocks(page) -> str | None:
    """
    Fallback visual text extractor using PyMuPDF dict layout.
    Catch text that page.get_text() may miss.
    """
    try:
        d = page.get_text("dict")
        items = []
        for b in d.get("blocks", []):
            if b.get("type") != 0:  # 0 = text block
                continue
            for line in b.get("lines", []):
                bbox = line.get("bbox", [0, 0, 0, 0])
                y0, x0 = bbox[1], bbox[0]
                text = "".join(
                    (s.get("text") or "")
                    for s in line.get("spans", [])
                ).strip()
                if text:
                    items.append((y0, x0, text))

        if not items:
            return None

        # Rough reading order: top → bottom, left → right
        items.sort(key=lambda t: (round(t[0], 1), t[1]))
        return "\n".join(t[2] for t in items).strip() or None
    except Exception:
        return None


def _need_ocr(raw_text: str, idx: int, suspect_pages: set[int]) -> bool:
    """
    Decide whether a page should enter OCR pipeline and emit explicit evaluation log.

    Rules:
    1. empty        -> always OCR
    2. short        -> OCR (raw text too little)
    3. suspect+short -> OCR (image page + sparse text)
    """

    raw_len = len(raw_text)
    in_suspect = idx in suspect_pages
    reasons = []

    # Rule 1: absolutely no text
    if not raw_text:
        reasons.append("empty")

    # Rule 2: text too short to be trusted
    elif raw_len < OCR_MIN_CHARS:
        reasons.append("short")

    # Rule 3: image page + sparse text
    elif in_suspect and raw_len < TEXT_LEN_THRESHOLD:
        reasons.append("suspect")

    need = bool(reasons)

    logger.info(
        "[PDF_OCR_DECISION] page=%d raw_len=%d reasons=%s",
        idx,
        raw_len,
        ",".join(reasons) if reasons else "none",
    )

    return need



def _extract_text_with_ocr_fallback(
    pages: dict[int, str],
    page_sources: dict[int, str],
    suspect_pages: set[int],
    doc,
) -> tuple[dict[int, str], dict[int, str]]:
    for idx, page in enumerate(doc, start=1):
        raw_text = pages.get(idx, "").strip()
        raw_len = len(raw_text)
        source_before = page_sources.get(idx, "none")

        ocr_text = None

        if _need_ocr(raw_text, idx, suspect_pages):
            try:
                ocr_text = _ocr_page_with_tesseract(page)
            except Exception as e:
                logger.warning("OCR failed on page %d: %s", idx, e)

        # decide replace
        if ocr_text and (
            not raw_text or
            (len(ocr_text) > OCR_MIN_CHARS and len(ocr_text) > raw_len * OCR_REPLACE_RATIO)
        ):
            pages[idx] = ocr_text
            page_sources[idx] = "ocr"
            final_source = "ocr"
            ocr_len = len(ocr_text)
        else:
            final_source = source_before
            ocr_len = len(ocr_text) if ocr_text else 0

        gain = ocr_len - raw_len if final_source == "ocr" else 0
        ratio = (ocr_len / raw_len) if raw_len > 0 and final_source == "ocr" else 0.0

        logger.info(
            "[PDF_PAGE_SOURCE] page=%d source=%s raw_len=%d ocr_len=%d gain=%d ratio=%.2f",
            idx,
            final_source,
            raw_len,
            ocr_len,
            gain,
            ratio,
        )

    return pages, page_sources



def _raw_extraction(
    pdf_bytes: bytes,
    *,
    filename: str = "<bytes>",
) -> tuple[dict[int, str], set[int], dict[int, str], dict[int, str]]:
    """
    PDF
    ├─ Page 1 → raw OK → 不 OCR
    ├─ Page 2 → raw 很短 → OCR
    ├─ Page 3 → 有图片 → OCR
    ├─ Page 4 → raw OK → 不 OCR
    Perform initial raw text extraction pass."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if not _unlock_doc_if_needed(doc, filename=filename):
            return {}, set(), {}, {}
        pages, suspect_pages, form_pages, annot_pages = {}, set(), {}, {}
        for idx, page in enumerate(doc, start=1):
            text = page.get_text()  #
            if not text or not text.strip():
                # visual fallback: catch text hidden in layout blocks
                text = _extract_visual_text_blocks(page)
            if text and text.strip():
                pages[idx] = text
            if page.get_images(full=True):
                suspect_pages.add(idx)
            
            form_text = _extract_form_fields(page)  #
            if form_text: form_pages[idx] = form_text
            
            annot_text = _extract_annotations(page)  #
            if annot_text: annot_pages[idx] = annot_text
            
        return pages, suspect_pages, form_pages, annot_pages


def _get_total_pages(pdf_bytes: bytes) -> int:
    """Return the total number of pages in the PDF."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return doc.page_count  #
    except Exception:
        return 0  #


def _extract_pdf_pages_and_sources( data: bytes, filename: str ) -> tuple[int, dict[int, str], dict[int, str], dict[int, str], dict[int, str]]:
    total_pages = _get_total_pages(data)
    pages, suspect_pages, form_pages, annot_pages = _raw_extraction(data, filename=filename)
    page_sources = {p: "raw" for p in pages}

    if total_pages == 0 and not pages and not form_pages and not annot_pages:
        return 0, {}, {}, {}, {}

    with fitz.open(stream=data, filetype="pdf") as doc:
        if not _unlock_doc_if_needed(doc, filename=filename):
            return 0, {}, {}, {}, {}
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
    raw_pages = len(pages)
    ocr_pages = sum(1 for v in page_sources.values() if v == "ocr")
    empty_pages = total_pages - len(pages)

    logger.info(
        "[PDF_PARSE_BLOCKS] file=%s total_pages=%d raw_pages=%d ocr_pages=%d empty_pages=%d form_blocks=%d annot_blocks=%d\n",
        filename,
        total_pages,
        raw_pages,
        ocr_pages,
        empty_pages,
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
