#!/usr/bin/env python3
"""
email_to_canonical_jsonl.py

New pipeline:
MBOX → CanonicalBlock JSONL

No Task, no chunk_xxx, no BatchProcessor.
"""

from pathlib import Path
import mailbox
import json
import re
import sys
from email import policy
from email.parser import BytesParser

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_parse_pdf_to_jsonl import parse_pdf_bytes_to_canonical_blocks
from helper.helper_parsing_doc import extract_text_from_doc, extract_text_from_docx, WORD_EXTS
from helper.helper_parsing_xls import extract_excel_text, XLS_EXTS

RAW_MBOX_DIR = Path("data/mbox/raw")
OUTPUT_JSONL = Path("data/mbox/jsonl/email_blocks.jsonl")


def normalize_date(raw_date: str) -> str:
    if not raw_date:
        return ""
    raw_date = re.sub(r"^[A-Za-z]{3},\s*", "", raw_date)
    raw_date = re.sub(r"\s+[+-]\d{4}(?:\s+\([^)]*\))?$", "", raw_date)
    return raw_date.strip()


def write_block(out, block: dict):
    out.write(json.dumps(block, ensure_ascii=False) + "\n")


def email_body_to_block(email, base_meta, seq):
    text = email.get_body(preferencelist=("plain", "html"))
    if not text:
        return None
    content = text.get_content().strip()
    if not content:
        return None

    return {
        "doc_id": f"email_{base_meta['email_id']}",
        "block_id": f"email_{base_meta['email_id']}_b{seq:05d}",
        "page": None,
        "source": "email_body",

        "part": "body",
        "file_type": "email",
        "attachment": None,

        "seq": seq,
        "char": len(content),
        "word": len(content.split()),
        "text": content,
        **base_meta,
    }


def attachment_text_to_block(text, base_meta, filename, seq, file_type):
    text = text.strip()
    if not text:
        return None

    doc_id = f"email_{base_meta['email_id']}::{filename}"

    return {
        "doc_id": doc_id,
        "block_id": f"{doc_id}_b{seq:05d}",
        "page": None,
        "source": "attachment_text",

        "part": "attachment",
        "file_type": file_type,
        "attachment": filename,

        "seq": seq,
        "char": len(text),
        "word": len(text.split()),
        "text": text,
        **base_meta,
    }


def main():
    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_JSONL.open("w", encoding="utf-8") as out:
        for mbox_file in RAW_MBOX_DIR.iterdir():
            print(f"[INFO] Processing {mbox_file}")
            mbox = mailbox.mbox(str(mbox_file))

            for raw in mbox:
                email = BytesParser(policy=policy.default).parsebytes(raw.as_bytes())

                email_id = email.get("Message-ID", "").strip()
                if not email_id:
                    continue

                base_meta = {
                    "email_id": email_id,
                    "thread_id": email.get("In-Reply-To", ""),
                    "from": email.get("From", ""),
                    "to": email.get("To", ""),
                    "subject": email.get("Subject", ""),
                    "date": normalize_date(email.get("Date", "")),
                }

                seq = 0

                # 1) Email body
                body_block = email_body_to_block(email, base_meta, seq + 1)
                if body_block:
                    seq += 1
                    write_block(out, body_block)

                # 2) Attachments
                for part in email.iter_attachments():
                    fn = part.get_filename()
                    if not fn:
                        continue

                    data = part.get_payload(decode=True)
                    if not data:
                        continue

                    ext = Path(fn).suffix.lower()
                    doc_id = f"email_{email_id}::{fn}"

                    # PDF → CanonicalBlock
                    if ext == ".pdf":
                        blocks = parse_pdf_bytes_to_canonical_blocks(
                            pdf_bytes=data,
                            filename=fn,
                            doc_id=doc_id,
                            part="attachment",
                            attachment=fn,
                        )
                        for b in blocks:
                            b.update(base_meta)
                            write_block(out, b)

                    # Word
                    elif ext in WORD_EXTS:
                        paras = (
                            extract_text_from_docx(data)
                            if ext == ".docx"
                            else extract_text_from_doc(data)
                        )
                        full_text = "\n\n".join(paras.values())
                        seq += 1
                        blk = attachment_text_to_block(full_text, base_meta, fn, seq, ext[1:])
                        if blk:
                            write_block(out, blk)

                    # Excel
                    elif ext in XLS_EXTS:
                        full_text = extract_excel_text(data, fn)
                        seq += 1
                        blk = attachment_text_to_block(full_text, base_meta, fn, seq, "excel")
                        if blk:
                            write_block(out, blk)

            mbox.close()

    print(f"[DONE] Canonical email JSONL written to {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
