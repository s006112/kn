#!/usr/bin/env python3
"""
parse_mbox_to_chunk.py
Responsibility:
Convert one or more local mbox files into a JSONL stream of "canonical block" records derived from each email body and
optionally from parsed attachments.

Pipelines:
- mbox_iterate -> message_parse -> metadata_build -> body_parse -> block_enrich -> jsonl_write -> attachment_save -> attachment_parse
- then `build_chunks_jsonl` post-processes blocks into final chunks and optional audit sidecar files

Invariants:
- Overwrites `data/mbox/jsonl/<mbox_stem>_blocks.jsonl` for each mbox file processed.
- Skips any message that lacks a `Message-ID` header.
- Saves each attachment payload to disk before attempting any attachment parsing.

Output files in `data/mbox/jsonl/`:
- `<mbox_stem>_blocks.jsonl`: canonical block-level records parsed directly from email bodies and supported attachments.
- `<mbox_stem>_chunks.jsonl`: retrieval/indexing-ready records after dropping very short blocks and splitting oversized blocks. This is the only file type from this pipeline intended to feed the next JSONL-to-FAISS stage.
- `<mbox_stem>_drop.jsonl`: optional audit log of blocks excluded from `_chunks.jsonl`, currently for `hard_min_words`.
- `<mbox_stem>_split_added.jsonl`: optional audit log for chunk splitting, including the original oversized parent block and each emitted child chunk.

Downstream handoff:
- Only `*_chunks.jsonl` is a FAISS input candidate.
- `*_blocks.jsonl`, `*_drop.jsonl`, and `*_split_added.jsonl` are inspection/audit artifacts and are not direct FAISS inputs.
- In the current repo, `rag/faiss_build.py` reads the specific path `data/mbox/jsonl/mbox_chunks.jsonl`, so per-mbox files such as `<mbox_stem>_chunks.jsonl` would need to be merged or renamed before using that exact entrypoint.

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


RAW_MBOX_DIR = ROOT_DIR / "data" / "mbox" / "raw"
OUTPUT_DIR = ROOT_DIR / "data" / "mbox" / "jsonl"

ATTACHMENT_PARSERS = {
    ".pdf":  parse_pdf_bytes_to_canonical_blocks,
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


def print_status(current: int, total: int, label: str):
    """
    Purpose:
    Print a minimal single-line terminal progress status for the current mbox file.

    Inputs:
    - current: Number of messages processed so far.
    - total: Total number of messages in the mbox file.
    - label: Human-readable label for the current mbox file.

    Outputs:
    - None. Writes a carriage-returned status line to stdout.
    """
    total = max(total, 1)
    percent = int(current * 100 / total)
    end = "\n" if current >= total else ""
    print(f"\r[INFO] {label}: {current}/{total} ({percent}%)", end=end, flush=True)

def main():
    """
    Purpose:
    Iterate all files in the raw mbox directory, parse each message, and emit canonical block JSONL per mbox file.

    Inputs:
    - None.

    Outputs:
    - None. Writes `data/mbox/jsonl/<mbox_stem>_blocks.jsonl`, then calls `build_chunks_jsonl(...)` to produce
      `data/mbox/jsonl/<mbox_stem>_chunks.jsonl` plus optional sidecar logs
      `data/mbox/jsonl/<mbox_stem>_drop.jsonl` and `data/mbox/jsonl/<mbox_stem>_split_added.jsonl`, and saves
      attachment payloads under `data/mbox/raw/<ext>/`. Of these outputs, only `<mbox_stem>_chunks.jsonl` is intended
      for the downstream FAISS-building stage.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for mbox_file in RAW_MBOX_DIR.iterdir():
        if mbox_file.is_dir():
            continue

        base_prefix = mbox_file.stem
        block_filename = f"{base_prefix}_blocks.jsonl"
        output_jsonl = OUTPUT_DIR / block_filename
        chunks_jsonl = OUTPUT_DIR / f"{base_prefix}_chunks.jsonl"

        if chunks_jsonl.exists():
            print(f"[SKIP] Chunks JSONL already exists: {chunks_jsonl}")
            continue

        print(f"[INFO] Processing {mbox_file}")
        mbox = mailbox.mbox(str(mbox_file))
        total_messages = len(mbox)

        with output_jsonl.open("w", encoding="utf-8") as out:
            for index, raw in enumerate(mbox, start=1):
                print_status(index, total_messages, mbox_file.name)
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
                    safe_fn = re.sub(r"[<>:\"/\\|?*]", "_", fn)
                    save_path = save_dir / f"{safe_email_id}__{safe_fn}"
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

        print(f"[DONE] Canonical email JSONL written to {output_jsonl}")

        print("[INFO] Building chunks jsonl...")
        build_chunks_jsonl(
            OUTPUT_DIR,
            block_filename,
            chunks_jsonl,
        )
        print(f"[DONE] Chunks JSONL written to {chunks_jsonl}")

if __name__ == "__main__":
    main()
