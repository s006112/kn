#!/usr/bin/env python3
"""
email_to_canonical_jsonl.py

New pipeline:
MBOX → CanonicalBlock JSONL

No Task, no chunk_xxx, no BatchProcessor.
(seq completely removed)
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

from helper.helper_parse_raw_to_jsonl import (
    parse_pdf_bytes_to_canonical_blocks,
    parse_email_bytes_to_canonical_blocks,
    parse_doc_bytes_to_canonical_blocks,
    parse_xls_bytes_to_canonical_blocks,
)

RAW_MBOX_DIR = Path("data/mbox/raw")
OUTPUT_JSONL = Path("data/mbox/jsonl/email_blocks.jsonl")

ATTACHMENT_PARSERS = {
    ".pdf":  parse_pdf_bytes_to_canonical_blocks,
    **{ext: parse_doc_bytes_to_canonical_blocks for ext in (".doc", ".docx")},
    **{ext: parse_xls_bytes_to_canonical_blocks for ext in (".xls", ".xlsx")},
}

def normalize_date(raw_date: str) -> str:
    if not raw_date:
        return ""
    raw_date = re.sub(r"^[A-Za-z]{3},\s*", "", raw_date)
    raw_date = re.sub(r"\s+[+-]\d{4}(?:\s+\([^)]*\))?$", "", raw_date)
    return raw_date.strip()


def write_block(out, block: dict):
    out.write(json.dumps(block, ensure_ascii=False) + "\n")

def main():
    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_JSONL.open("w", encoding="utf-8") as out:
        for mbox_file in RAW_MBOX_DIR.iterdir():
            if mbox_file.is_dir():
                continue

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

                # 1) Email body
                blocks = parse_email_bytes_to_canonical_blocks(email, email_id)
                for b in blocks:
                    b.update(base_meta)
                    write_block(out, b)

                # 2) Attachments
                for part in email.iter_attachments():
                    fn = part.get_filename()
                    if not fn:
                        continue

                    data = part.get_payload(decode=True)
                    if not data:
                        continue

                    ext = Path(fn).suffix.lower()
                    if not ext:
                        continue

                    # ---------- RAW SAVE (generic) ----------
                    suffix = ext[1:]              # ".pdf" -> "pdf"
                    save_dir = RAW_MBOX_DIR / suffix
                    save_dir.mkdir(exist_ok=True)

                    safe_email_id = re.sub(r"[<>:\"/\\|?*]", "_", email_id)
                    save_path = save_dir / f"{safe_email_id}__{fn}"
                    if not save_path.exists():
                        save_path.write_bytes(data)
                    # ---------------------------------------

                    doc_id = f"email_{email_id}::{fn}"

                    parser = ATTACHMENT_PARSERS.get(ext)
                    if not parser:
                        continue

                    blocks = parser(data, fn, doc_id)
                    for b in blocks:
                        b.update(base_meta)
                        write_block(out, b)

            mbox.close()

    print(f"[DONE] Canonical email JSONL written to {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
