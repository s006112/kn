"""
parse_raw_to_jsonl.py

Responsibility:
Convert parsed "raw blocks" from multiple document sources into a canonical block
stream suitable for downstream JSONL serialization.

Used by:
- rag/parse_mbox_to_chunk.py
- rag/parse_standard_to_block.py

Pipelines:
- email_bytes -> raw_blocks -> canonical_blocks
- pdf_bytes -> page_blocks -> raw_blocks -> canonical_blocks
- doc_bytes -> paragraph_blocks -> raw_blocks -> canonical_blocks
- xls_bytes -> sheet_text -> raw_blocks -> canonical_blocks

Invariants:
- Empty or whitespace-only block text is dropped.
- Canonical blocks always include: page, char, word, part, text.
- Canonical block sequence numbers are local to the conversion call and are not
  emitted in the output.

Out of scope:
- PDF/email/DOC/XLS parsing implementations (delegated to helper parsers).
- JSONL writing or persistence.
- Emitting file_type, attachment, doc_id, block_id, or source fields in canonical
  blocks.
"""

from parse_pdf_to_raw import get_pdf_page_blocks
from parse_email_to_raw import parse_email_to_raw_blocks
from parse_doc_to_raw import get_doc_paragraph_blocks
from parse_xls import extract_excel_text

def raw_blocks_to_canonical_blocks(raw_blocks, part, file_type, attachment=None):
    """
    Purpose:
    Yield canonical blocks from an iterable of raw blocks.

    Inputs:
    - raw_blocks: iterable of dicts with at least a "text" field and optional "page"
      and "part" fields.
    - part: default part label used when a raw block does not provide "part".
    - file_type: unused input retained for compatibility with callers.
    - attachment: unused input retained for compatibility with callers.

    Outputs:
    - Yields dicts with keys: page, char, word, part, text.

    Side effects:
    - None.

    Failure modes:
    - KeyError if a raw block is missing the "text" field.
    - AttributeError if raw["text"] is not a string-like object supporting strip().
    """
    seq = 0
    for raw in raw_blocks:
        text = raw["text"].strip()
        if not text:
            continue

        seq += 1
        yield {
            "page": raw.get("page"),
            "char": len(text),
            "word": len(text.split()),

            "part": raw.get("part", part),
            "file_type": file_type,
            "attachment": attachment,

            "doc_id": raw["doc_id"],
            "block_id": f"{raw['doc_id']}_b{seq:02d}",
            "source": raw.get("source"),

            "text": text,
        }


def parse_email_bytes_to_canonical_blocks(email, email_id):
    """
    Purpose:
    Parse an email payload into canonical blocks.

    Inputs:
    - email: email bytes or string accepted by parse_email_to_raw_blocks().
    - email_id: identifier forwarded to parse_email_to_raw_blocks().

    Outputs:
    - An iterator of canonical blocks, or an empty list if no raw blocks were
      produced.

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions raised by parse_email_to_raw_blocks().
    - Propagates exceptions raised by raw_blocks_to_canonical_blocks() for invalid
      raw blocks.
    """
    raw_blocks = parse_email_to_raw_blocks(email, email_id)
    if not raw_blocks:
        return []

    # 直接一次餵進去，讓 seq 在同一 email 內自然累加
    return raw_blocks_to_canonical_blocks(
        raw_blocks,
        part=None,            # part 由 raw 自己帶
        file_type="email",
        attachment=None,
    )


def parse_pdf_bytes_to_canonical_blocks(data: bytes, filename: str, doc_id: str):
    """
    Purpose:
    Parse PDF bytes into canonical blocks using page-level parsing output.

    Inputs:
    - data: PDF file bytes.
    - filename: PDF filename forwarded to get_pdf_page_blocks().
    - doc_id: identifier attached to intermediate raw blocks (not emitted).

    Outputs:
    - An iterator of canonical blocks with part="attachment".

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions raised by get_pdf_page_blocks().
    - Propagates exceptions raised by raw_blocks_to_canonical_blocks() for invalid
      raw blocks.
    """
    blocks_by_page = get_pdf_page_blocks(data, filename=filename)

    raw_blocks = []
    for page in sorted(blocks_by_page.keys()):
        for block in blocks_by_page[page]:
            text = block["text"].strip()
            if not text:
                continue

            raw_blocks.append({
                "doc_id": doc_id,
                "text": text,
                "page": page,
                "source": block["source"],
            })

    return raw_blocks_to_canonical_blocks(
        raw_blocks,
        part="attachment",
        file_type="pdf",
        attachment=filename,
    )


def parse_doc_bytes_to_canonical_blocks(data: bytes, filename: str, doc_id: str):
    """
    Purpose:
    Parse DOC/DOCX bytes into canonical blocks using paragraph-level parsing output.

    Inputs:
    - data: DOC/DOCX file bytes.
    - filename: filename forwarded to get_doc_paragraph_blocks() and used to derive
      the extension.
    - doc_id: identifier attached to intermediate raw blocks (not emitted).

    Outputs:
    - An iterator of canonical blocks with part="attachment".

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions raised by get_doc_paragraph_blocks().
    - Propagates exceptions raised by raw_blocks_to_canonical_blocks() for invalid
      raw blocks.
    """
    para_blocks = get_doc_paragraph_blocks(data, filename)

    raw_blocks = []
    for para_idx in sorted(para_blocks):
        for blk in para_blocks[para_idx]:
            text = blk["text"].strip()
            if not text:
                continue

            raw_blocks.append({
                "doc_id": doc_id,
                "text": text,
                "page": para_idx,
                "source": blk["source"],  # "doc" or "docx"
            })

    ext = filename.split(".")[-1].lower()

    return raw_blocks_to_canonical_blocks(
        raw_blocks,
        part="attachment",
        file_type=ext,
        attachment=filename,
    )


def parse_xls_bytes_to_canonical_blocks(data: bytes, filename: str, doc_id: str):
    """
    Purpose:
    Parse XLS/XLSX bytes into canonical blocks by extracting all text.

    Inputs:
    - data: XLS/XLSX file bytes.
    - filename: filename forwarded to extract_excel_text() and used to derive the
      extension.
    - doc_id: identifier attached to intermediate raw blocks (not emitted).

    Outputs:
    - An iterator of canonical blocks with part="attachment", or an empty list if
      no text was extracted.

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions raised by extract_excel_text().
    - Propagates exceptions raised by raw_blocks_to_canonical_blocks() for invalid
      raw blocks.
    """
    text = extract_excel_text(data, filename)
    if not text:
        return []

    raw_blocks = [{
        "doc_id": doc_id,
        "text": text,
        "page": None,
        "source": "xls",
    }]

    ext = filename.split(".")[-1].lower()

    return raw_blocks_to_canonical_blocks(
        raw_blocks,
        part="attachment",
        file_type=ext,
        attachment=filename,
    )
