"""
helper_parse_doc_to_raw.py
Responsibility:
Extract paragraph-level raw blocks from Word documents with unified fallback logic.
"""

from pathlib import Path
import logging
from helper.helper_parsing_doc import extract_text_from_doc, extract_text_from_docx, WORD_EXTS

logger = logging.getLogger(__name__)

def get_doc_paragraph_blocks(data: bytes, filename: str) -> dict[int, list[dict]]:
    """
    Return paragraph-indexed blocks, aligned with PDF get_pdf_page_blocks style.

    { para_idx : [ { "source": "...", "text": "..." } ] }
    """
    ext = Path(filename).suffix.lower()

    if ext == ".docx":
        paras = extract_text_from_docx(data)
        source = "docx"
    elif ext == ".doc":
        paras = extract_text_from_doc(data)
        source = "doc"
    else:
        return {}

    blocks_by_para = {}
    for idx, text in paras.items():
        if text and text.strip():
            blocks_by_para[idx] = [{
                "source": source,
                "text": text.strip()
            }]

    logger.info(
        "[DOC_PARSE_BLOCKS] file=%s paragraphs=%d",
        filename,
        len(blocks_by_para)
    )

    return blocks_by_para
