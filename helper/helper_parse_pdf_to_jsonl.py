# helper_parse_pdf_to_jsonl.py
from __future__ import annotations

from helper.helper_parse_pdf_to_raw import get_pdf_page_blocks


def parse_pdf_bytes_to_canonical_blocks(
    pdf_bytes: bytes,
    filename: str,
    doc_id: str,
    part: str = "document",
    attachment: str | None = None,
):
    """
    Canonical PDF → CanonicalBlock generator.

    Responsibility:
    - Take raw PDF bytes
    - Use raw parser to extract blocks
    - Emit CanonicalBlock dicts
    """

    blocks_by_page = get_pdf_page_blocks(pdf_bytes, filename=filename)

    seq = 0
    for page in sorted(blocks_by_page.keys()):
        for block in blocks_by_page[page]:
            text = block["text"].strip()
            if not text:
                continue

            seq += 1
            yield {
                "doc_id": doc_id,
                "block_id": f"{doc_id}_b{seq:05d}",
                "page": page,
                "source": block["source"],  # raw, ocr, hybrid

                "part": part,
                "file_type": "pdf",
                "attachment": attachment,

                "seq": seq,
                "char": len(text),
                "word": len(text.split()),
                "text": text,
            }
