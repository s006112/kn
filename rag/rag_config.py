import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import psutil


PROJECT_ROOT = Path(__file__).resolve().parent
RAW_MBOX_DIR = (PROJECT_ROOT / "data/raw/mbox").resolve()
OUTPUT_JSONL = (PROJECT_ROOT / "data/clean/email_chunks.jsonl").resolve()
PROCESSED_MBOX_TXT = (PROJECT_ROOT / "data/clean/processed_mboxes.txt").resolve()


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
