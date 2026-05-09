""" p.py
Start and supervise the local file-processing system that watches configured folders,
loads prompts, creates pipeline context, starts queue workers, schedules watchdog
handlers, exposes status, and shuts the system down.

Pipelines:
- torrent watch folder scan -> torrent detection -> file lock -> safe move -> w folder
- audio watch -> audio queue -> wav convert -> transcribe -> text write -> audio archive
- ttml watch -> ready check -> file lock -> ttml convert -> text write -> ttml archive
- pretext watch -> pretext queue -> llm pretext -> write outputs -> pretext archive
- extract watch -> extract queue -> llm extract -> merge markdown -> distill -> extract archive
- notes watch -> unlink clean -> link backup
"""

from __future__ import annotations
from pathlib import Path
import os
import sys

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, os.fspath(BASE_DIR / "w"))

LOG_DIR = BASE_DIR / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

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

    "PRETEXT_SUFFIX": ".txt",
    "EXTRACT_SUFFIX": ("_p.txt", ".md"),

    "PRETEXT_PROMPT": None,
    "EXTRACT_PROMPT": None,
    "DISTILL_PROMPT": None,
}

import logging
import threading
from logging.handlers import RotatingFileHandler
from typing import Any, NamedTuple
from watchdog.observers import Observer
from w.p_context import PipelineContext, create_pipeline_context
from w.p_pipelines import (
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
from w.utils_files import read_prompt_file
from w.utils_unlink import setup_wikilink_cleaner_logging

class UTFStreamHandler(logging.StreamHandler):
    """Write formatted log records to a byte stream using UTF-8."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = self.stream
            stream.buffer.write(msg.encode("utf-8"))
            stream.buffer.write(self.terminator.encode("utf-8"))
            self.flush()
        except Exception:
            self.handleError(record)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        RotatingFileHandler(
            LOG_DIR / "script.log",
            maxBytes=1 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        ),
        UTFStreamHandler(sys.stdout),
    ],
)

CURRENT_CONTEXT: PipelineContext | None = None

REQUIRED_DIR_KEYS = (
    "ORIGINAL_FOLDER",
    "AUDIO_DONE_FOLDER",
    "LINK_BACKUP_FOLDER",
    "FAIL_FOLDER",
)

FALSE_VALUES = {"0", "false", "no", "off", "disable", "disabled"}


class SystemHandles(NamedTuple):
    """Runtime handles returned by start_system."""

    context: PipelineContext
    threads: dict[str, threading.Thread]
    observer: Any


def ensure_directories(cfg: dict[str, Any]) -> None:
    """Create runtime folders required before workers start."""
    for key in REQUIRED_DIR_KEYS:
        Path(cfg[key]).mkdir(parents=True, exist_ok=True)


def start_thread(
    threads: dict[str, threading.Thread],
    name: str,
    target: Any,
    args: tuple[Any, ...],
) -> None:
    """Create and start one daemon worker thread."""
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    threads[name] = thread
    thread.start()


def run_periodic_file_scanner(ctx: PipelineContext) -> None:
    intervals = ctx.config.get("INTERVALS", {})
    scan_seconds = intervals.get("SCAN_SECONDS")

    while not ctx.shutdown_flag.is_set():
        if ctx.shutdown_flag.wait(scan_seconds):
            return
        file_scanner(ctx)


def start_system(cfg: dict[str, Any] | None = None) -> SystemHandles:
    """Initialize prompts, context, workers, scans, and watchdog observers."""
    if cfg is None:
        raise ValueError("Configuration dictionary is required.")

    pipeline_overrides = cfg.get("PIPELINES") or {}
    pipelines = {}

    for key, default in CONFIG["PIPELINES"].items():
        value = pipeline_overrides.get(key, default)
        pipelines[key] = (
            value.strip().lower() not in FALSE_VALUES
            if isinstance(value, str)
            else bool(value)
        )

    cfg = {
        **cfg,
        "PIPELINES": pipelines,
        "PRETEXT_PROMPT": read_prompt_file("prompt_pretext.txt"),
        "EXTRACT_PROMPT": read_prompt_file("prompt_extract.txt"),
        "DISTILL_PROMPT": read_prompt_file("prompt_distill.txt"),
    }

    setup_wikilink_cleaner_logging(logging.getLogger())
    ensure_directories(cfg)

    ctx = create_pipeline_context(cfg)

    global CURRENT_CONTEXT
    CURRENT_CONTEXT = ctx

    text_enabled = pipelines["PRETEXT"] or pipelines["EXTRACT"]
    pretext_handler = extract_handler = premium_extract_handler = None

    if text_enabled:
        (
            pretext_handler,
            extract_handler,
            premium_extract_handler,
        ) = create_pipeline_handlers(ctx)

    threads: dict[str, threading.Thread] = {}

    if pipelines["TTML"]:
        start_thread(threads, "TTMLPipeline", process_ttml_pipeline, (ctx,))

    if pipelines["PRETEXT"]:
        start_thread(
            threads,
            "TextPipeline-Pretext",
            process_pretext_queue,
            (ctx,),
        )

    if pipelines["EXTRACT"]:
        start_thread(
            threads,
            "TextPipeline-Extract",
            process_extract_queue,
            (ctx, extract_handler),
        )
        start_thread(
            threads,
            "TextPipeline-PremiumExtract",
            process_premium_extract_queue,
            (ctx, premium_extract_handler),
        )

    if pipelines["AUDIO"]:
        start_thread(threads, "AudioPipeline-GPU", process_audio_pipeline, (ctx,))

    start_thread(threads, "PeriodicScanner", run_periodic_file_scanner, (ctx,))

    if pipelines["NOTES"]:
        start_thread(threads, "WikilinkCleaner", process_wikilink_cleaning, (ctx,))

    if pipelines["X_URL_DOWNLOAD"]:
        start_thread(
            threads,
            "XUrlDownloadPipeline",
            process_x_url_download_pipeline,
            (ctx,),
        )

    watch_specs = []
    if pipelines["PRETEXT"]:
        watch_specs.append((pretext_handler, "PRETEXT_WATCH_FOLDER"))

    if pipelines["EXTRACT"]:
        watch_specs.extend(
            (
                (extract_handler, "EXTRACT_WATCH_FOLDER"),
                (premium_extract_handler, "PREMIUM_WATCH_FOLDER"),
            )
        )

    observer = None
    if watch_specs:
        observer = Observer()
        for handler, folder_key in watch_specs:
            observer.schedule(handler, os.fspath(cfg[folder_key]), recursive=False)
        observer.start()

    enabled_names = [key for key, enabled in pipelines.items() if enabled]
    logging.info(
        "Enabled pipelines: %s",
        ", ".join(enabled_names) if enabled_names else "none",
    )

    return SystemHandles(context=ctx, threads=threads, observer=observer)


def stop_system(handles: SystemHandles) -> None:
    """Signal shutdown, stop the observer, and clear global context."""
    handles.context.shutdown_flag.set()

    try:
        if handles.observer is not None:
            handles.observer.stop()
            handles.observer.join()
    except Exception as exc:
        logging.warning("Failed to stop observer cleanly: %s", exc)
    finally:
        global CURRENT_CONTEXT
        CURRENT_CONTEXT = None


def system_status() -> dict[str, Any]:
    """Return pipeline flags, queue sizes, and wikilink cleaner counters."""
    if CURRENT_CONTEXT is None:
        raise RuntimeError("System context not initialized.")

    ctx = CURRENT_CONTEXT
    return {
        "pipelines": dict(ctx.config["PIPELINES"]),
        "queues": {
            "pretext": ctx.pretext_queue.qsize(),
            "extract": ctx.extract_queue.qsize(),
            "premium_extract": ctx.premium_extract_queue.qsize(),
        },
        "wikilink_cleaner": {
            "last_run": ctx.wikilink_cleaning_stats["last_run"],
            "cycle_count": ctx.wikilink_cleaning_stats["cycle_count"],
        },
    }


def main(cfg: dict[str, Any] | None = None) -> None:
    """Start the system and keep the supervising process alive."""
    resolved_cfg = cfg or CONFIG

    logging.info("Starting local file-processing system")
    handles = start_system(resolved_cfg)

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop_system(handles)
        logging.info("Local file-processing system stopped")


if __name__ == "__main__":
    main()