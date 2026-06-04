"""
Responsibility:
Convert parsed email "raw blocks" into a canonical block stream suitable for
downstream JSONL serialization.

Used by:
* helper/test_mbox_to_jsonl.py

Pipelines:
- email_bytes -> raw_blocks -> canonical_blocks

Invariants:
- Empty or whitespace-only block text is dropped.
- Canonical blocks always include: page, char, word, part, text.
- Canonical block sequence numbers are local to the conversion call and are not
  emitted in the output.

Out of scope:
- PDF/DOC/XLS parsing implementations.
- JSONL writing or persistence.
- Emitting file_type, attachment, doc_id, block_id, or source fields in canonical
  blocks.
"""

from tool.test_parse_email_to_raw_based import (
    parse_email_to_raw_blocks as parse_email_to_raw_blocks_based,
)
from tool.test_parse_email_to_raw_enhanced import (
    parse_email_to_raw_blocks as parse_email_to_raw_blocks_enhanced,
)

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
            #"file_type": file_type,
            #"attachment": attachment,

            #doc_id": raw["doc_id"],
            #"block_id": f"{raw['doc_id']}_b{seq:02d}",
            #"source": raw.get("source"),

            "text": text,
        }


def parse_email_bytes_to_canonical_blocks(
    email,
    email_id,
    *,
    raw_parser=parse_email_to_raw_blocks_enhanced,
):
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
    raw_blocks = raw_parser(email, email_id)
    if not raw_blocks:
        return []

    # 直接一次餵進去，讓 seq 在同一 email 內自然累加
    return raw_blocks_to_canonical_blocks(
        raw_blocks,
        part=None,            # part 由 raw 自己帶
        file_type="email",
        attachment=None,
    )


def parse_email_bytes_to_canonical_blocks_based(email, email_id):
    return parse_email_bytes_to_canonical_blocks(
        email,
        email_id,
        raw_parser=parse_email_to_raw_blocks_based,
    )


def parse_email_bytes_to_canonical_blocks_enhanced(email, email_id):
    return parse_email_bytes_to_canonical_blocks(
        email,
        email_id,
        raw_parser=parse_email_to_raw_blocks_enhanced,
    )
