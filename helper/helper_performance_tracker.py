"""
helper_performance_tracker.py

Responsibility:
Provides a lightweight performance tracker for the email RAG scripts.

Used by:
* rag/mbox_to_json.py

Pipelines:
- track_performance -> log_summary

Invariants:
- `PerformanceTracker` updates are protected by a lock.
"""

import logging
import threading
import time
from collections import deque

import psutil


class PerformanceTracker:
    """
    Responsibility:
    Collects simple throughput statistics for batch processing and optionally tracks system memory usage over time.

    Invariants:
    - Updates to `stats` are protected by `self.lock`.
    - `batch_efficiency` and `cpu_memory_used` are bounded deques.
    """

    def __init__(self) -> None:
        """
        Purpose:
        Initialize counters and bounded history buffers for performance tracking.

        Inputs:
        - None.

        Outputs:
        - None.

        Side effects:
        - Allocates `self.stats` and `self.lock`.

        Failure modes:
        - None.
        """

        self.stats = {
            "emails_processed": 0,
            "chunks_created": 0,
            "cpu_time": 0.0,
            "batch_efficiency": deque(maxlen=50),
            # Keep a short rolling window for summary logging.
            "cpu_memory_used": deque(maxlen=100),
        }
        self.lock = threading.Lock()

    # -- update helpers -----------------------------------------------------
    def update_batch(self, emails: int, chunks: int, duration: float) -> None:
        """
        Purpose:
        Accumulate counters for a completed processing batch and record per-batch throughput.

        Inputs:
        - emails: Number of emails processed in the batch.
        - chunks: Number of chunks produced in the batch.
        - duration: Wall-clock seconds spent on the batch.

        Outputs:
        - None.

        Side effects:
        - Mutates `self.stats` under `self.lock`.

        Failure modes:
        - None (division-by-zero is handled by recording 0 efficiency when `duration <= 0`).
        """

        with self.lock:
            self.stats["emails_processed"] += emails
            self.stats["chunks_created"] += chunks
            self.stats["cpu_time"] += duration
            eff = emails / duration if duration > 0 else 0
            self.stats["batch_efficiency"].append(eff)

    # -- monitoring ---------------------------------------------------------
    def record_cpu_memory(self, mem: float) -> None:
        """
        Purpose:
        Append a single memory-usage sample to the rolling history.

        Inputs:
        - mem: Memory usage percentage (e.g., `psutil.virtual_memory().percent`).

        Outputs:
        - None.

        Side effects:
        - Mutates `self.stats["cpu_memory_used"]` under `self.lock`.

        Failure modes:
        - None.
        """

        with self.lock:
            self.stats["cpu_memory_used"].append(mem)

    def monitor_loop(self) -> None:
        """
        Purpose:
        Run an infinite monitoring loop that periodically samples system memory usage.

        Inputs:
        - None.

        Outputs:
        - None (never returns).

        Side effects:
        - Calls `record_cpu_memory` once per second when `psutil` is truthy.
        - Sleeps for 1 second per iteration.

        Failure modes:
        - Never terminates on its own; callers must run it in a dedicated thread/process if needed.
        """

        while True:
            if psutil:
                self.record_cpu_memory(psutil.virtual_memory().percent)
            time.sleep(1)

    # -- summary ------------------------------------------------------------
    def log_summary(self) -> None:
        """
        Purpose:
        Emit an aggregated performance summary to the logger.

        Inputs:
        - None.

        Outputs:
        - None.

        Side effects:
        - Emits `logging.info` messages including derived rates.

        Failure modes:
        - None.
        """

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
