import logging
import threading
import time
from queue import Queue
from types import SimpleNamespace

from .helper_files import get_next_available_filename
from helper.helper_llm import LLMPermanentFailure

_file_locks = {}
_file_locks_mutex = threading.Lock()


def create_runtime(config):
    return SimpleNamespace(
        config=config,
        pretext_queue=Queue(),
        extract_queue=Queue(),
        premium_extract_queue=Queue(),
        audio_queue=Queue(),
        ttml_queue=Queue(),
        text_processing_lock=threading.Lock(),
        audio_processing_lock=threading.Lock(),
        processed_files_global=set(),
        processed_files_lock=threading.Lock(),
        wikilink_cleaning_stats={"last_run": None, "cycle_count": 0},
        shutdown_flag=threading.Event(),
    )
def process_queue(runtime, queue, process, method_name, scan_files=None):
    intervals = runtime.config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)
    scan_seconds = intervals.get("SCAN_SECONDS", 60)
    next_scan = time.monotonic()

    while True:
        if scan_files and time.monotonic() >= next_scan:
            try:
                scan_files(runtime)
            except Exception as e:
                logging.error("%s scan error: %s", method_name, e)
            next_scan = time.monotonic() + scan_seconds

        if queue.empty():
            time.sleep(wait_seconds)
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

        time.sleep(wait_seconds)
