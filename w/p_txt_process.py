from __future__ import annotations

import logging
import threading
import time
from queue import Queue

from helper.helper_llm import LLMPermanentFailure
from w.helper_files import get_next_available_filename
from w.p_extract import (
    process_extract_file,
    process_premium_extract_file,
    scan_extract_files,
    scan_premium_extract_files,
)
from w.p_pretext import process_pretext_file, scan_pretext_files

_file_locks = {}
_file_locks_mutex = threading.Lock()


def process_queue(config, queue, process, method_name, scan_files=None, shutdown_flag=None, *scan_args):
    intervals = config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)
    scan_seconds = intervals.get("SCAN_SECONDS", 60)
    next_scan = time.monotonic()

    while shutdown_flag is None or not shutdown_flag.is_set():
        if scan_files and time.monotonic() >= next_scan:
            try:
                scan_files(*scan_args)
            except Exception as e:
                logging.error("%s scan error: %s", method_name, e)
            next_scan = time.monotonic() + scan_seconds

        if queue.empty():
            if shutdown_flag is None:
                time.sleep(wait_seconds)
            else:
                shutdown_flag.wait(wait_seconds)
            continue

        file_path = queue.get()
        locked = False

        try:
            with _file_locks_mutex:
                lock = _file_locks.setdefault(file_path, threading.Lock())

            locked = lock.acquire(blocking=False)

            if not locked:
                queue.put(file_path)
            else:
                try:
                    process(file_path, get_next_available_filename)
                except LLMPermanentFailure as e:
                    logging.error(
                        "Resilient Queue: OpenAI API permanent failure for file %s "
                        "(model: %s): %s",
                        e.file_path,
                        e.model,
                        e.reason,
                    )
                except Exception as e:
                    logging.error("%s queue error: %s", method_name, e)

        except Exception as e:
            logging.error("%s queue error: %s", method_name, e)

        finally:
            if locked:
                with _file_locks_mutex:
                    _file_locks.pop(file_path, None)
                lock.release()

            queue.task_done()

        if shutdown_flag is None:
            time.sleep(wait_seconds)
        else:
            shutdown_flag.wait(wait_seconds)


def start_text_processing(config, shutdown_flag):
    pretext_queue = Queue()
    extract_queue = Queue()
    premium_extract_queue = Queue()
    processed_files_global = set()
    processed_files_lock = threading.Lock()

    threads = {
        name: threading.Thread(target=target, args=args, daemon=True, name=name)
        for enabled, name, target, args in [
            (config["PIPELINES"]["PRETEXT"], "TextPipeline-Pretext", process_queue, (config, pretext_queue, lambda path, _next: process_pretext_file(config, path, processed_files_global, processed_files_lock), "process_pretext", scan_pretext_files, shutdown_flag, config, pretext_queue, processed_files_global, processed_files_lock)),
            (config["PIPELINES"]["EXTRACT"], "TextPipeline-Extract", process_queue, (config, extract_queue, lambda path, _next: process_extract_file(config, path, _next), "process_extract", scan_extract_files, shutdown_flag, config, extract_queue)),
            (config["PIPELINES"]["EXTRACT"], "TextPipeline-PremiumExtract", process_queue, (config, premium_extract_queue, lambda path, _next: process_premium_extract_file(config, path, _next), "process_premium_extract", scan_premium_extract_files, shutdown_flag, config, premium_extract_queue)),
        ]
        if enabled
    }

    for thread in threads.values():
        thread.start()

    return threads
