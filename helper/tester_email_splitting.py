#!/usr/bin/env python3
import time
import json
import logging
import mailbox
from pathlib import Path
from email import policy
from email.parser import BytesParser

import helper_parse_email_to_raw as email_raw_mod
from helper_parse_email_to_raw import parse_email_to_raw_blocks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

RAW_MBOX_DIR = Path("data/mbox/raw")
OUT_JSONL = Path("data/mbox/jsonl/email_split_compare.jsonl")

LARGE_BLOCK_CHAR = 1000

def fake_should_split_threads(text: str, *, char_threshold: int = 2000) -> bool:
    return False

def run_once(email, email_id):
    t0 = time.perf_counter()
    blocks = parse_email_to_raw_blocks(email, email_id)
    t1 = time.perf_counter()
    return blocks, round((t1 - t0) * 1000, 3)

def compute_metrics(blocks):
    chars = [len(b["text"]) for b in blocks]
    body_chars = [len(b["text"]) for b in blocks if b["part"] == "body"]

    return {
        "block_count": len(blocks),
        "body_block_count": len(body_chars),
        "max_block_char": max(chars) if chars else 0,
        "max_body_block_char": max(body_chars) if body_chars else 0,
        "large_block_count": sum(1 for c in chars if c >= LARGE_BLOCK_CHAR),
    }

def main():
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    with OUT_JSONL.open("w", encoding="utf-8") as out:
        for mbox_file in RAW_MBOX_DIR.iterdir():
            if mbox_file.is_dir():
                continue

            logging.info("processing mbox=%s", mbox_file.name)
            mbox = mailbox.mbox(str(mbox_file))

            for raw in mbox:
                email = BytesParser(policy=policy.default).parsebytes(raw.as_bytes())
                email_id = email.get("Message-ID", "").strip()
                if not email_id:
                    continue

                text_part = email.get_body(preferencelist=("plain", "html"))
                content = text_part.get_content() if text_part else ""

                base_meta = {
                    "email_id": email_id,
                    "thread_id": email.get("In-Reply-To", ""),
                    "subject": email.get("Subject", ""),
                    "date": email.get("Date", ""),
                    "from": email.get("From", ""),
                    "to": email.get("To", ""),
                    "char_len": len(content),
                }

                orig_fn = email_raw_mod.should_split_threads

                email_raw_mod.should_split_threads = fake_should_split_threads
                base_blocks, base_ms = run_once(email, email_id)

                email_raw_mod.should_split_threads = orig_fn
                enh_blocks, enh_ms = run_once(email, email_id)

                base_m = compute_metrics(base_blocks)
                enh_m = compute_metrics(enh_blocks)

                record = {
                    **base_meta,
                    "baseline": {
                        **base_m,
                        "elapsed_ms": base_ms,
                    },
                    "enhanced": {
                        **enh_m,
                        "elapsed_ms": enh_ms,
                    },
                    "split_gain": (
                        round(base_m["max_block_char"] / enh_m["max_block_char"], 2)
                        if enh_m["max_block_char"] > 0
                        else None
                    ),
                }

                out.write(json.dumps(record, ensure_ascii=False) + "\n")

                logging.info(
                    "email=%s blocks %d→%d max_char %d→%d gain=%s",
                    email_id,
                    base_m["block_count"],
                    enh_m["block_count"],
                    base_m["max_block_char"],
                    enh_m["max_block_char"],
                    record["split_gain"],
                )

            mbox.close()

    logging.info("written %s", OUT_JSONL)

if __name__ == "__main__":
    main()
