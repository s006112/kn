# helper_parse_raw_to_jsonl.py

from helper.helper_parse_pdf_to_raw import get_pdf_page_blocks
from helper.helper_parse_email_to_raw import parse_email_body_to_raw_block


def raw_blocks_to_canonical_blocks(raw_blocks, part, file_type, attachment=None):
    seq = 0
    for raw in raw_blocks:
        text = raw["text"].strip()
        if not text:
            continue

        seq += 1
        yield {
            "doc_id": raw["doc_id"],
            "block_id": f"{raw['doc_id']}_b{seq:05d}",
            "page": raw.get("page"),
            "source": raw.get("source"),

            "part": part,
            "file_type": file_type,
            "attachment": attachment,

            "char": len(text),
            "word": len(text.split()),
            "text": text,
        }


def parse_pdf_bytes_to_canonical_blocks(pdf_bytes, filename, doc_id, part="document", attachment=None):
    blocks_by_page = get_pdf_page_blocks(pdf_bytes, filename=filename)

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
        part=part,
        file_type="pdf",
        attachment=attachment,
    )


def parse_email_bytes_to_canonical_blocks(email, email_id):
    raw_block = parse_email_body_to_raw_block(email, email_id)
    if not raw_block:
        return []

    return raw_blocks_to_canonical_blocks(
        [raw_block],
        part="email",
        file_type=None,
        attachment=None,
    )
