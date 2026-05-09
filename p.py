# p.py


from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, NamedTuple

from watchdog.observers import Observer

BASE_DIR = Path(__file__).resolve().parent

from w.p_pipelines import (
    PipelineContext,
    create_pipeline_handlers,
    file_scanner,
    process_audio_pipeline,
    process_extract_queue,
    process_premium_extract_queue,
    process_pretext_queue,
    process_ttml_pipeline,
    process_wikilink_cleaning,
    process_x_url_download_pipeline,
)
from w.utils_files import configure_logging, read_prompt_file

WHISPER_FOLDER = Path("/desktop/Sync/Whisper")
WATCH_FOLDER = Path("/desktop")

CONFIG = {
    "MODEL_PRETEXT": "gpt-4.1-mini",
    "MODEL_DISTILL": "o3",
    "MODEL_EXTRACT_MATRIX": {
        "EXTRACT_WATCH_FOLDER": [
            "gpt-5.4-mini",
            "grok-4.20-non-reasoning",  # grok-4.3, grok-4-1-fast-non-reasoning
            "gemini-3.1-pro-preview",  # gemini-3.1-flash-lite-preview
        ],
        "PREMIUM_WATCH_FOLDER": [
            "gpt-5.4",
        ],
    },
    "PIPELINES": {
        "AUDIO": True,
        "TTML": True,
        "PRETEXT": True,
        "EXTRACT": True,
        "NOTES": True,
        "X_URL_DOWNLOAD": True,
    },
    "INTERVALS": {
        "SCAN_SECONDS": 60,
        "WAIT_SECONDS": 1.0,
        "LLM_MAX_RETRIES": 2,
        "LLM_RETRY_DELAY_SECONDS": 10,
        "LLM_TIMEOUT_SECONDS": 60,
        "X_RESOLVE_TIMEOUT_SECONDS": 10,
    },
    "WATCH_FOLDER": WATCH_FOLDER,
    "WHISPER_FOLDER": WHISPER_FOLDER,
    "TTML_WATCH_FOLDER": WATCH_FOLDER,
    "EXTRACT_WATCH_FOLDER": WATCH_FOLDER,
    "AUDIO_TRANSCRIBED_TXT_FOLDER": WATCH_FOLDER,
    "AUDIO_WATCH_FOLDERS": (
        WATCH_FOLDER,
        WHISPER_FOLDER,
    ),
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
    "X_URL_LIST_FILE": WHISPER_FOLDER / "X" / "X.txt",
    "DOWNLOAD_TARGET_FOLDER": WHISPER_FOLDER / "X",
    "LOG_DIR": BASE_DIR / "data" / "logs",
    "PRETEXT_SUFFIX": ".txt",
    "EXTRACT_SUFFIX": ("_p.txt", ".md"),
    "PRETEXT_PROMPT": read_prompt_file("prompt_pretext.txt"),
    "EXTRACT_PROMPT": read_prompt_file("prompt_extract.txt"),
    "DISTILL_PROMPT": read_prompt_file("prompt_distill.txt"),
}

configure_logging(CONFIG["LOG_DIR"])


class SystemHandles(NamedTuple):
    """Runtime handles returned by start_system."""
    context: PipelineContext
    threads: dict[str, threading.Thread]
    observer: Any


def run_file_scanner(ctx: PipelineContext) -> None:
    scan_seconds = ctx.config["INTERVALS"]["SCAN_SECONDS"]

    file_scanner(ctx)  # Initial scan on startup

    while not ctx.shutdown_flag.is_set():
        if ctx.shutdown_flag.wait(scan_seconds):
            return
        file_scanner(ctx)


def start_runtime(ctx: PipelineContext) -> tuple[dict[str, threading.Thread], Any]:
    pretext_handler, extract_handler, premium_extract_handler = create_pipeline_handlers(ctx)
    threads: dict[str, threading.Thread] = {}

    worker_specs = [
        ("TTML", "TTMLPipeline", process_ttml_pipeline, (ctx,)),
        ("PRETEXT", "TextPipeline-Pretext", process_pretext_queue, (ctx,)),
        ("EXTRACT", "TextPipeline-Extract", process_extract_queue, (ctx, extract_handler)),
        ("EXTRACT", "TextPipeline-PremiumExtract", process_premium_extract_queue, (ctx, premium_extract_handler)),
        ("AUDIO", "AudioPipeline-GPU", process_audio_pipeline, (ctx,)),
        (None, "PeriodicScanner", run_file_scanner, (ctx,)),
        ("NOTES", "WikilinkCleaner", process_wikilink_cleaning, (ctx,)),
        ("X_URL_DOWNLOAD", "XUrlDownloadPipeline", process_x_url_download_pipeline, (ctx,)),
    ]

    for flag, name, target, args in worker_specs:
        if flag is None or ctx.config["PIPELINES"][flag]:
            thread = threading.Thread(target=target, args=args, daemon=True, name=name)
            threads[name] = thread
            thread.start()

    watch_specs = [
        ("PRETEXT", pretext_handler, "PRETEXT_WATCH_FOLDER"),
        ("EXTRACT", extract_handler, "EXTRACT_WATCH_FOLDER"),
        ("EXTRACT", premium_extract_handler, "PREMIUM_WATCH_FOLDER"),
    ]

    observer = None
    for flag, handler, folder_key in watch_specs:
        if ctx.config["PIPELINES"][flag]:
            if observer is None:
                observer = Observer()
            observer.schedule(handler, os.fspath(ctx.config[folder_key]), recursive=False)
    if observer is not None:
        observer.start()

    return threads, observer


def stop_system(handles: SystemHandles) -> None:
    handles.context.shutdown_flag.set()

    if handles.observer is not None:
        handles.observer.stop()
        handles.observer.join()


def main() -> None:
    ctx = PipelineContext(CONFIG)
    threads, observer = start_runtime(ctx)
    handles = SystemHandles(context=ctx, threads=threads, observer=observer)
    logging.info("Enabled pipelines: %s", ", ".join(key for key, enabled in ctx.config["PIPELINES"].items() if enabled) or "none")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop_system(handles)

if __name__ == "__main__":
    main()
