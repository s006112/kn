#!/usr/bin/env python3
"""
Responsibility:
Convert one or more local mbox files into a JSONL stream of "canonical block" records derived from each email body and
optionally from parsed attachments.

Pipelines:
- mbox_iterate -> message_parse -> metadata_build -> body_parse -> block_enrich -> jsonl_write -> attachment_save -> attachment_parse

Invariants:
- Overwrites `data/mbox/jsonl/email_blocks.jsonl` on each run.
- Skips any message that lacks a `Message-ID` header.
- Saves each attachment payload to disk before attempting any attachment parsing.

Out of scope:
- IMAP fetching or mailbox synchronization.
- Deduplication across mbox files or across messages.
- Thread reconstruction beyond copying `In-Reply-To` into `thread_id`.
"""

from pathlib import Path
import mailbox
import json
import re
import sys
from email import policy
from email.parser import BytesParser

ROOT_DIR = Path(__file__).resolve().parent.parent  # ← 关键
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parse_block_to_chunk import build_chunks_jsonl
from parse_raw_to_jsonl import (
    parse_pdf_bytes_to_canonical_blocks,
    parse_email_bytes_to_canonical_blocks,
    parse_doc_bytes_to_canonical_blocks,
    parse_xls_bytes_to_canonical_blocks,
)


BLOCK_SUFFIX = "mbox_blocks.jsonl"
RAW_MBOX_DIR = ROOT_DIR / "data" / "mbox" / "raw"
OUTPUT_JSONL = ROOT_DIR / "data" / "mbox" / "jsonl" / f"{BLOCK_SUFFIX}"
CHUNKS_JSONL = ROOT_DIR / "data" / "mbox" / "jsonl" / f"{BLOCK_SUFFIX.replace('blocks', 'chunks')}"

ATTACHMENT_PARSERS = {
    #".pdf":  parse_pdf_bytes_to_canonical_blocks,
    #**{ext: parse_doc_bytes_to_canonical_blocks for ext in (".doc", ".docx")},
    #**{ext: parse_xls_bytes_to_canonical_blocks for ext in (".xls", ".xlsx")},
}

def normalize_date(raw_date: str) -> str:
    """
    Purpose:
    Normalize an email `Date` header value by removing a leading weekday and stripping a trailing timezone suffix.

    Inputs:
    - raw_date: Raw date header string.

    Outputs:
    - Normalized date string, or an empty string if input is falsy.
    """
    if not raw_date:
        return ""
    raw_date = re.sub(r"^[A-Za-z]{3},\s*", "", raw_date)
    raw_date = re.sub(r"\s+[+-]\d{4}(?:\s+\([^)]*\))?$", "", raw_date)
    return raw_date.strip()


def write_block(out, block: dict):
    """
    Purpose:
    Write a single canonical block dict as one JSON object line to an open JSONL output stream.

    Inputs:
    - out: Writable text file-like object.
    - block: JSON-serializable dict representing a canonical block.

    Outputs:
    - None. Writes one line to `out`.
    """
    if "text" in block:
        text_value = block.pop("text")
        block["text"] = text_value
    out.write(json.dumps(block, ensure_ascii=False) + "\n")

def main():
    """
    Purpose:
    Iterate all files in the raw mbox directory, parse each message, and emit canonical block JSONL to a fixed output path.

    Inputs:
    - None.

    Outputs:
    - None. Writes JSONL to `data/mbox/jsonl/email_blocks.jsonl` and saves attachment payloads under `data/mbox/raw/<ext>/`.
    """
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
                    "subject": email.get("Subject", ""),
                    "date": normalize_date(email.get("Date", "")),
                    "from": email.get("From", ""),
                    "to": email.get("To", ""),
                    "email_id": email_id,
                    "thread_id": email.get("In-Reply-To", ""),
                }

                # Body blocks are always emitted when message parsing succeeds and Message-ID is present.
                blocks = parse_email_bytes_to_canonical_blocks(email, email_id)
                for b in blocks:
                    b.update(base_meta)
                    write_block(out, b)

                # Attachments are saved generically; parsing is optional and controlled by ATTACHMENT_PARSERS.
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

                    # Saving raw payloads preserves inputs for later parsing runs or offline inspection.
                    suffix = ext[1:]              # ".pdf" -> "pdf"
                    save_dir = RAW_MBOX_DIR / suffix
                    save_dir.mkdir(exist_ok=True)

                    safe_email_id = re.sub(r"[<>:\"/\\|?*]", "_", email_id)
                    save_path = save_dir / f"{safe_email_id}__{fn}"
                    if not save_path.exists():
                        save_path.write_bytes(data)

                    doc_id = f"{email_id}::{fn}"

                    parser = ATTACHMENT_PARSERS.get(ext)
                    if not parser:
                        continue

                    blocks = parser(data, fn, doc_id)
                    for b in blocks:
                        b.update(base_meta)
                        write_block(out, b)

            mbox.close()

    print(f"[DONE] Canonical email JSONL written to {OUTPUT_JSONL}")

    print("[INFO] Building chunks jsonl...")
    build_chunks_jsonl(
        OUTPUT_JSONL.parent,
        BLOCK_SUFFIX,
        CHUNKS_JSONL,
    )
    print(f"[DONE] Chunks JSONL written to {CHUNKS_JSONL}")

if __name__ == "__main__":
    main()
