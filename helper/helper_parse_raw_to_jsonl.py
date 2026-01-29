# helper_parse_raw_to_jsonl.py

from helper.helper_parse_pdf_to_raw import get_pdf_page_blocks
from helper.helper_parse_email_to_raw import parse_email_to_raw_blocks
from helper.helper_parse_doc_to_raw import get_doc_paragraph_blocks
from helper.helper_parsing_xls import extract_excel_text

def raw_blocks_to_canonical_blocks(raw_blocks, part, file_type, attachment=None):
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
    DOC/DOCX bytes → RawBlock → CanonicalBlock
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
    XLS/XLSX bytes → RawBlock → CanonicalBlock
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