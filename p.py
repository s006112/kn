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

import codecs
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, NamedTuple

from watchdog.observers import Observer

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, os.fspath(BASE_DIR / "w"))

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
        logging.StreamHandler(
            codecs.getwriter("utf-8")(sys.stdout.buffer)
            if getattr(sys.stdout, "buffer", None) is not None
            else sys.stdout
        ),
    ],
)


class SystemHandles(NamedTuple):
    """Runtime handles returned by start_system."""

    context: PipelineContext
    threads: dict[str, threading.Thread]
    observer: Any


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


def run_file_scanner(ctx: PipelineContext) -> None:
    intervals = ctx.config.get("INTERVALS", {})
    scan_seconds = intervals.get("SCAN_SECONDS")

    file_scanner(ctx)  # Initial scan on startup

    while not ctx.shutdown_flag.is_set():
        if ctx.shutdown_flag.wait(scan_seconds):
            return
        file_scanner(ctx)


def prepare_runtime_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if cfg is None:
        raise ValueError("Configuration dictionary is required.")

    overrides = cfg.get("PIPELINES") or {}
    pipelines = {
        key: bool(overrides.get(key, default))
        for key, default in CONFIG["PIPELINES"].items()
    }

    return {
        **cfg,
        "PIPELINES": pipelines,
        "PRETEXT_PROMPT": read_prompt_file("prompt_pretext.txt"),
        "EXTRACT_PROMPT": read_prompt_file("prompt_extract.txt"),
        "DISTILL_PROMPT": read_prompt_file("prompt_distill.txt"),
    }


def create_runtime_handlers(ctx: PipelineContext) -> dict[str, Any]:
    pipelines = ctx.config["PIPELINES"]
    if not (pipelines["PRETEXT"] or pipelines["EXTRACT"]):
        return {}

    pretext, extract, premium_extract = create_pipeline_handlers(ctx)
    return {
        "pretext": pretext,
        "extract": extract,
        "premium_extract": premium_extract,
    }


def start_runtime_workers(
    ctx: PipelineContext,
    handlers: dict[str, Any],
) -> dict[str, threading.Thread]:
    pipelines = ctx.config["PIPELINES"]
    threads: dict[str, threading.Thread] = {}

    specs = [
        ("TTML", "TTMLPipeline", process_ttml_pipeline, (ctx,)),
        ("PRETEXT", "TextPipeline-Pretext", process_pretext_queue, (ctx,)),
        ("EXTRACT", "TextPipeline-Extract", process_extract_queue, (ctx, handlers.get("extract"))),
        ("EXTRACT", "TextPipeline-PremiumExtract", process_premium_extract_queue, (ctx, handlers.get("premium_extract"))),
        ("AUDIO", "AudioPipeline-GPU", process_audio_pipeline, (ctx,)),
        (None, "PeriodicScanner", run_file_scanner, (ctx,)),
        ("NOTES", "WikilinkCleaner", process_wikilink_cleaning, (ctx,)),
        ("X_URL_DOWNLOAD", "XUrlDownloadPipeline", process_x_url_download_pipeline, (ctx,)),
    ]

    for flag, name, target, args in specs:
        if flag is None or pipelines[flag]:
            start_thread(threads, name, target, args)

    return threads


def start_runtime_observer(
    ctx: PipelineContext,
    handlers: dict[str, Any],
) -> Any:
    pipelines = ctx.config["PIPELINES"]

    specs = [
        ("PRETEXT", handlers.get("pretext"), "PRETEXT_WATCH_FOLDER"),
        ("EXTRACT", handlers.get("extract"), "EXTRACT_WATCH_FOLDER"),
        ("EXTRACT", handlers.get("premium_extract"), "PREMIUM_WATCH_FOLDER"),
    ]

    watch_specs = [
        (handler, folder_key)
        for flag, handler, folder_key in specs
        if pipelines[flag] and handler is not None
    ]

    if not watch_specs:
        return None

    observer = Observer()
    for handler, folder_key in watch_specs:
        observer.schedule(
            handler,
            os.fspath(ctx.config[folder_key]),
            recursive=False,
        )
    observer.start()
    return observer


def start_system(cfg: dict[str, Any] | None = None) -> SystemHandles:
    """Initialize config, context, workers, scanner, and watchdog observer."""
    cfg = prepare_runtime_config(cfg)
    ctx = create_pipeline_context(cfg)

    handlers = create_runtime_handlers(ctx)
    threads = start_runtime_workers(ctx, handlers)
    observer = start_runtime_observer(ctx, handlers)

    logging.info("Enabled pipelines: %s", ", ".join(key for key, enabled in ctx.config["PIPELINES"].items() if enabled) or "none")

    return SystemHandles(context=ctx, threads=threads, observer=observer)



def stop_system(handles: SystemHandles) -> None:
    """Signal shutdown and stop the observer."""
    handles.context.shutdown_flag.set()

    if handles.observer is not None:
        handles.observer.stop()
        handles.observer.join()


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
