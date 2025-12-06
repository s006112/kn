from dataclasses import dataclass, field
import logging
import os
import re
import sys
import threading
import time
import mailbox
import psutil
from collections import deque
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils_text_processing import extract_email_body_tasks
from chunk_json import Task, BatchProcessor, JsonlWriter
from chunk_att import extract_attachment_tasks

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
RAW_MBOX_DIR = (PROJECT_ROOT / "data/raw/mbox").resolve()
OUTPUT_JSONL = (PROJECT_ROOT / "data/clean/email_chunks.jsonl").resolve()
PROCESSED_MBOX_TXT = (PROJECT_ROOT / "data/clean/processed_mboxes.txt").resolve()

# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------
@dataclass
class Config:
    # Path and processing settings
    raw_mbox_dir: Path = field(default=RAW_MBOX_DIR, init=False)
    output_jsonl: Path = field(default=OUTPUT_JSONL, init=False)
    processed_mbox_txt: Path = field(default=PROCESSED_MBOX_TXT, init=False)
    chunk_size: int = 2000
    chunk_overlap: int = 200
    min_chunk_size: int = 100
    max_text_len: int = 50000  # max characters for bodies or pages

    # Runtime options
    batch_size: int = 128
    parallel_workers: int = field(init=False)

    def __post_init__(self):
        # 初始化多線程並行數（上限為 32）
        cpu_cores = os.cpu_count() or 8
        self.parallel_workers = min(cpu_cores, 32)

        # 確保路徑存在
        self.raw_mbox_dir.mkdir(parents=True, exist_ok=True)
        self.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
        self.processed_mbox_txt.parent.mkdir(parents=True, exist_ok=True)

        # logging summary
        logging.info("PARALLEL_WORKERS = %s", self.parallel_workers)
        logging.info("BATCH_SIZE = %s", self.batch_size)

# ---------------------------------------------------------------------------
# Performance tracker for monitoring and summaries
# ---------------------------------------------------------------------------
class PerformanceTracker:
    def __init__(self) -> None:
        self.stats = {
            "emails_processed": 0,
            "chunks_created": 0,
            "cpu_time": 0.0,
            "batch_efficiency": deque(maxlen=50),
            "cpu_memory_used": deque(maxlen=100),  # ✅ 補上這行
        }
        self.lock = threading.Lock()

    # -- update helpers -----------------------------------------------------
    def update_batch(self, emails: int, chunks: int, duration: float) -> None:
        with self.lock:
            self.stats["emails_processed"] += emails
            self.stats["chunks_created"] += chunks
            self.stats["cpu_time"] += duration
            eff = emails / duration if duration > 0 else 0
            self.stats["batch_efficiency"].append(eff)

    # -- monitoring ---------------------------------------------------------

    def record_cpu_memory(self, mem: float) -> None:
        with self.lock:
            self.stats["cpu_memory_used"].append(mem)

    def monitor_loop(self) -> None:
        while True:
            if psutil:
                self.record_cpu_memory(psutil.virtual_memory().percent)
            time.sleep(1)

    # -- summary ------------------------------------------------------------

    def log_summary(self) -> None:
        total_time = self.stats["cpu_time"]
        emails_sec = self.stats["emails_processed"] / total_time if total_time > 0 else 0
        chunks_sec = self.stats["chunks_created"] / total_time if total_time > 0 else 0

        logging.info("=" * 60)
        logging.info("PERFORMANCE SUMMARY")
        logging.info("=" * 60)

        if self.stats.get("cpu_memory_used"):
            avg_cpu = sum(self.stats["cpu_memory_used"]) / len(self.stats["cpu_memory_used"])
            logging.info("CPU Memory: %.1f%%", avg_cpu)

        logging.info("CPU Time: %.1fs", total_time)
        logging.info("Throughput: %.2f email/sec, %.2f chunks/sec", emails_sec, chunks_sec)
        logging.info(
            "Total processed: %d emails, %d chunks",
            self.stats["emails_processed"],
            self.stats["chunks_created"],
        )

        if self.stats["batch_efficiency"]:
            avg_eff = sum(self.stats["batch_efficiency"]) / len(self.stats["batch_efficiency"])
            logging.info("Batch Efficiency: %.2f email/sec per batch", avg_eff)

        logging.info("=" * 60)

# ---------------------------------------------------------------------------
# Message parsing utilities
# ---------------------------------------------------------------------------

def parse_emails(
    emails: Iterable[bytes],
    cfg: Config,
) -> tuple[List[Task], int]:
    """Convert raw emails into processing tasks and count bodies."""
    tasks: List[Task] = []
    body_count = 0  # track how many plain text bodies were extracted

    for raw in emails:
        email_id = "unknown"
        try:
            email = BytesParser(policy=policy.default).parsebytes(raw)
            email_id = email.get("Message-ID", "")
            raw_date = email.get("Date", "")
            date = raw_date
            if raw_date:
                # Remove optional weekday prefix like "Wed, "
                date = re.sub(r"^[A-Za-z]{3},\s*", "", raw_date)
                # Drop trailing numeric timezone such as "+0800"
                date = re.sub(r"\s+[+-]\d{4}(?:\s+\([^)]*\))?$", "", date)
                date = date.strip()
            base = {
                "email_id": email_id,
                "thread_id": email.get("In-Reply-To", ""),
                "from": email.get("From", ""),
                "to": email.get("To", ""),
                "subject": email.get("Subject", ""),
                "date": date,
            }

            # ✅ 跳過 metadata 全部缺失的情況
            if all(not base.get(k) for k in ["email_id", "from", "subject"]):
                logging.warning("Skipping message due to missing metadata: %s", base)
                continue

            # ✅ 提取正文（支援 text/html fallback）
            body_pairs = extract_email_body_tasks(email, base, cfg.max_text_len)
            body_tasks = _to_tasks(body_pairs)
            if body_tasks:
                tasks.extend(body_tasks)
                body_count += 1

            attachment_tasks = extract_attachment_tasks(email, base, cfg.max_text_len)
            if attachment_tasks:
                tasks.extend(attachment_tasks)

        except Exception as e:
            logging.error("Failed to parse message %s: %s", email_id, e, exc_info=True)

    return tasks, body_count

# ---------------------------------------------------------------------------
# Utility functions for processed file tracking
# ---------------------------------------------------------------------------

def load_processed(cfg: Config) -> set[str]:
    if cfg.processed_mbox_txt.exists():
        return set(cfg.processed_mbox_txt.read_text().splitlines())
    return set()

def mark_processed(cfg: Config, name: str) -> None:
    with cfg.processed_mbox_txt.open("a", encoding="utf-8") as handle:
        handle.write(name + "\n")

# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def _to_tasks(items: Iterable[Tuple[str, dict]]) -> List[Task]:
    return [Task(text, meta) for text, meta in items]
# ---------------------------------------------------------------------------
# Batch processing of a group of raw emails
# ---------------------------------------------------------------------------

def process_batch(
    emails: List[bytes],     # 原始邮件字节流列表（一个 batch）
    folder: str,               # 当前处理的邮箱文件名（如 inbox_july）
    processor: BatchProcessor, # 任务处理器（切块）
    writer: JsonlWriter,       # JSONL 写入器
    tracker: PerformanceTracker,  # 性能追踪器
) -> None:
    if not emails:
        return
    logging.info(
        "Processing batch of %d emails from %s", len(emails), folder
    )

    # Parse raw emails into text tasks
    parse_start = time.time()
    tasks, body_count = parse_emails(emails, processor.cfg)
    if not tasks:
        logging.warning("❗ No tasks to process in batch from %s — skipping", folder)  # Fix no task when remove text extraction
        return
    parse_time = time.time() - parse_start

    chunks = processor.process(tasks)     # 对 Task 切块
    chunk_count = writer.write_chunks(chunks)     # 写入 JSONL 文件
    tracker.update_batch(len(emails), chunk_count, parse_time)

# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------

def bootstrap() -> Config:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "extract.log"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return Config()

# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = bootstrap()
    tracker = PerformanceTracker()
    monitor = threading.Thread(target=tracker.monitor_loop, daemon=True)
    monitor.start()

    processed = load_processed(cfg)
    mbox_files = list(cfg.raw_mbox_dir.iterdir())
    logging.info("Found %d mailboxes", len(mbox_files))
    processor = BatchProcessor(cfg, tracker)
    total_emails = 0
    total_chunks_written = 0
    start_time = time.time()

    with JsonlWriter(cfg.output_jsonl) as writer:
        for mbox_file in mbox_files:
            if mbox_file.name in processed:
                logging.info("Skipping %s", mbox_file.name)
                continue

            logging.info("📥 Processing mailbox: %s", mbox_file.name)
            try:
                mbox = mailbox.mbox(str(mbox_file))
            except Exception as exc:
                logging.error("Error opening %s: %s", mbox_file.name, exc)
                continue

            batch: List[bytes] = []
            start_chunk_count = writer.chunk_count

            for idx, key in enumerate(mbox.iterkeys(), 1):
                batch.append(mbox.get_bytes(key))
                if len(batch) >= cfg.batch_size:
                    process_batch(batch, mbox_file.stem, processor, writer, tracker)
                    total_emails += len(batch)
                    batch = []
                    if (idx // cfg.batch_size) % 10 == 0:
                        tracker.log_summary()
            if batch:
                process_batch(batch, mbox_file.stem, processor, writer, tracker)
                total_emails += len(batch)

            written = writer.chunk_count - start_chunk_count
            logging.info("✅ Wrote %d chunks from %s", written, mbox_file.name)

            mbox.close()
            mark_processed(cfg, mbox_file.name)

        total_chunks_written = writer.chunk_count

    tracker.log_summary()
    elapsed = time.time() - start_time
    logging.info("🎯 TOTAL: %d emails, %d chunks, %.1fs, %.2f email/sec",
                 total_emails, total_chunks_written, elapsed,
                 total_emails / elapsed if elapsed else 0)

    logging.info("📁 JSONL file written to: %s", cfg.output_jsonl.resolve())

if __name__ == "__main__":
    main()
