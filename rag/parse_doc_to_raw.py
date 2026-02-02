#!/usr/bin/env python3
"""
parse_doc_to_raw.py

Used by:
- rag/parse_raw_to_jsonl.py

Responsibility:
Extract paragraph-level raw blocks from Word documents with unified fallback logic.
Structure is aligned with helper_parse_pdf_to_raw.py:
PDF  -> { page_idx : [ {source, text}, ... ] }
Word -> { para_idx : [ {source, text}, ... ] }

This version ONLY does paragraph extraction. No chunk splitting yet.
"""

from pathlib import Path
import logging

from parse_doc_helper import (
    extract_text_from_doc,
    extract_text_from_docx,
    WORD_EXTS,
)

logger = logging.getLogger(__name__)


def get_doc_paragraph_blocks(data: bytes, filename: str) -> dict[int, list[dict]]:
    """
    Return paragraph-indexed blocks.

    Output format:
    {
        para_idx: [
            {
                "source": "docx" | "doc",
                "text": "..."
            }
        ]
    }
    """
    ext = Path(filename).suffix.lower()

    if ext == ".docx":
        paras = extract_text_from_docx(data)
        source = "docx"
    elif ext == ".doc":
        paras = extract_text_from_doc(data)
        source = "doc"
    else:
        logger.warning("[DOC_PARSE_BLOCKS] Unsupported extension: %s", ext)
        return {}

    blocks_by_para: dict[int, list[dict]] = {}

    for idx, text in paras.items():
        if not text:
            continue
        text = text.strip()
        if not text:
            continue

        blocks_by_para[idx] = [{
            "source": source,
            "text": text,
        }]

    logger.info(
        "[DOC_PARSE_BLOCKS] file=%s ext=%s paragraphs=%d",
        filename,
        ext,
        len(blocks_by_para),
    )

    return blocks_by_para
