from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_llm import LLMPermanentFailure
from w.helper_files import configure_logging, get_next_available_filename
from w.p_audio import process_audio_pipeline
from w.p_extract import (
    create_extract_processors,
    scan_extract_files,
    scan_premium_extract_files,
)
from w.p_pretext import process_pretext_file, scan_pretext_files
from w.p_torrent import process_torrent_pipeline
from w.p_ttml import process_ttml_pipeline
from w.p_wiki import process_wikilink_cleaning
from w.p_ytd import process_ytd_pipeline

WATCH_FOLDER = Path("/desktop")
WHISPER_FOLDER = Path("/desktop/Sync/Whisper")

CONFIG = {
    "MODEL_PRETEXT": "gpt-4.1-mini",
    "MODEL_DISTILL": "o3",
    "MODEL_EXTRACT_MATRIX": {
        "EXTRACT_WATCH_FOLDER": [
            "gpt-5.4-mini",
            "grok-4.20-non-reasoning",  # grok-4.3, grok-4-1-fast-non-reasoning
            "gemini-3.1-flash-lite-preview",  # gemini-3.1-flash-lite-preview, gemini-3.1-pro-preview"
        ],
        "PREMIUM_WATCH_FOLDER": ["gpt-5.4"],
    },
    "PIPELINES": {
        "TORRENT": True,
        "AUDIO": True,
        "TTML": True,
        "PRETEXT": True,
        "EXTRACT": True,
        "WIKI": True,
        "YTD": True,
    },
    "INTERVALS": {
        "SCAN_SECONDS": 60,
        "WAIT_SECONDS": 1.0,
        "LLM_MAX_RETRIES": 2,
        "LLM_RETRY_DELAY_SECONDS": 10,
        "LLM_TIMEOUT_SECONDS": 60,
        "YTD_RESOLVE_TIMEOUT_SECONDS": 10,
    },
    "WATCH_FOLDER": WATCH_FOLDER,
    "WHISPER_FOLDER": WHISPER_FOLDER,
    "TTML_WATCH_FOLDER": WATCH_FOLDER,
    "EXTRACT_WATCH_FOLDER": WATCH_FOLDER,
    "AUDIO_TRANSCRIBED_TXT_FOLDER": WATCH_FOLDER,
    "AUDIO_WATCH_FOLDERS": (WATCH_FOLDER, WHISPER_FOLDER),
    "AUDIO_DONE_FOLDER": Path("/desktop/YT1"),
    "PRETEXT_WATCH_FOLDER": WATCH_FOLDER,
    "PREMIUM_WATCH_FOLDER": WHISPER_FOLDER / "Fail" / "p",
    "PRETEXT_DONE_FOLDER": WHISPER_FOLDER / "_p",
    "ARCHIVE_FOLDER": WHISPER_FOLDER / "_p",
    "ORIGINAL_FOLDER": WHISPER_FOLDER / "_p" / "Raw",
    "EXTRACT_FOLDER": WHISPER_FOLDER / "_p" / "Extract",
    "LINK_BACKUP_FOLDER": WHISPER_FOLDER / "_p" / "link_backup",
    "FAIL_FOLDER": WHISPER_FOLDER / "Fail",
    "OBSIDIAN_SYNC_FOLDER": Path("/desktop/Obsidian/O_2025"),
    "YTD_LIST_FILE": WHISPER_FOLDER / "X" / "X.txt",
    "DOWNLOAD_TARGET_FOLDER": WHISPER_FOLDER / "X",
    "LOG_DIR": ROOT_DIR / "data" / "logs",
    "PRETEXT_SUFFIX": ".txt",
    "EXTRACT_SUFFIX": ("_p.txt", ".md"),
    "PRETEXT_PROMPT": (ROOT_DIR / "prompt" / "prompt_pretext.txt").read_text(encoding="utf-8").strip(),
    "EXTRACT_PROMPT": (ROOT_DIR / "prompt" / "prompt_extract.txt").read_text(encoding="utf-8").strip(),
    "DISTILL_PROMPT": (ROOT_DIR / "prompt" / "prompt_distill.txt").read_text(encoding="utf-8").strip(),
}

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


def start_runtime(runtime) -> dict[str, threading.Thread]:
    extract_processor, premium_extract_processor = create_extract_processors(runtime)
    threads = {
        name: threading.Thread(target=target, args=args, daemon=True, name=name)
        for enabled, name, target, args in [
            (runtime.config["PIPELINES"]["TORRENT"], "TorrentPipeline", process_torrent_pipeline, (runtime,)),
            (runtime.config["PIPELINES"]["TTML"], "TTMLPipeline", process_ttml_pipeline, (runtime,)),
            (
                runtime.config["PIPELINES"]["PRETEXT"],
                "TextPipeline-Pretext",
                lambda runtime: process_queue(
                    runtime,
                    runtime.pretext_queue,
                    lambda path, _next: process_pretext_file(
                        runtime.config,
                        path,
                        runtime.processed_files_global,
                        runtime.processed_files_lock,
                    ),
                    "process_pretext",
                    scan_pretext_files,
                ),
                (runtime,),
            ),
            (
                runtime.config["PIPELINES"]["EXTRACT"],
                "TextPipeline-Extract",
                lambda runtime, processor: process_queue(
                    runtime,
                    runtime.extract_queue,
                    processor.process_extract,
                    "process_extract",
                    scan_extract_files,
                ),
                (runtime, extract_processor),
            ),
            (
                runtime.config["PIPELINES"]["EXTRACT"],
                "TextPipeline-PremiumExtract",
                lambda runtime, processor: process_queue(
                    runtime,
                    runtime.premium_extract_queue,
                    processor.process_premium_extract,
                    "process_premium_extract",
                    scan_premium_extract_files,
                ),
                (runtime, premium_extract_processor),
            ),
            (runtime.config["PIPELINES"]["AUDIO"], "AudioPipeline-GPU", process_audio_pipeline, (runtime,)),
            (runtime.config["PIPELINES"]["WIKI"], "WikilinkCleaner", process_wikilink_cleaning, (runtime,)),
            (runtime.config["PIPELINES"]["YTD"], "YTDPipeline", process_ytd_pipeline, (runtime,)),
        ]
        if enabled
    }

    for thread in threads.values():
        thread.start()

    return threads


def main() -> None:
    configure_logging(CONFIG["LOG_DIR"])

    runtime = create_runtime(CONFIG)
    start_runtime(runtime)

    logging.info("Enabled pipelines: %s", ", ".join(key for key, enabled in runtime.config["PIPELINES"].items() if enabled) or "none")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.shutdown_flag.set()


if __name__ == "__main__":
    main()
