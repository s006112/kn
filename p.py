import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, NamedTuple, Optional

from watchdog.observers import Observer

# Make w modules importable when running from repo root.
PROJECT_DIR = Path(__file__).resolve().parent
W_DIR = PROJECT_DIR / "w"
sys.path.insert(0, os.fspath(PROJECT_DIR))
sys.path.insert(0, os.fspath(W_DIR))

from w.p_context import PipelineContext, create_pipeline_context
from w.p_pipelines import (
    create_pipeline_handlers,
    periodic_file_scanner,
    process_audio_pipeline,
    process_extract_queue,
    process_premium_extract_queue,
    process_pretext_queue,
    process_ttml_pipeline,
    process_wikilink_cleaning,
    process_x_url_download_pipeline,
    scan_existing_files,
)
from w.utils_files import read_prompt_file
from w.utils_unlink import setup_wikilink_cleaner_logging

# sonar $1, sonar-pro $15, sonar-reasoning-pro $8
# gemini-2.5-flash-lite-preview-09-2025 $0.4, gemini-3.1-flash-lite-preview $1.5, gemini-3-pro-preview $12, 
# gpt-5-mini, gpt-5-nano, gpt-4.1-mini, gpt-4.1-nano, gpt-4o-mini, o1-mini, o3-mini, o4-mini,
# gpt-5.4 $15, gpt-5.2 $14, gpt-5.1 $10, gpt-4.1 $8, gpt-4o, o1 $60, o3 $8,
# grok-4-1-fast-reasoning $0.2, grok-4-1-fast-non-reasoning $0.2, grok-4.20-0309-non-reasoning $2.0
MODEL_PRETEXT = "gpt-4.1-mini"
#MODEL_PRETEXT = "sonar"
#MODEL_DISTILL = "sonar-reasoning-pro"
MODEL_DISTILL = "o3"
MODEL_EXTRACT_MATRIX = {
    "EXTRACT_WATCH_FOLDER": [
        #"sonar",
        #'gemini-3.1-flash-lite-preview',
        #"gemini-2.5-flash-lite-preview-09-2025",
        "gpt-5.4-mini",
        "grok-4.3",
        #"gpt-5.4"
        "gemini-3.1-pro-preview",

    ],
    "PREMIUM_WATCH_FOLDER": [
        "gpt-5.4",   # gpt-5.2, gpt-5.4
    ],
}

WHISPER_FOLDER = Path("/desktop/Sync/Whisper")
WATCH_FOLDER = Path("/desktop")
AUDIO_DONE_FOLDER = Path("/desktop/YT1")
OBSIDIAN_SYNC_FOLDER = Path("/desktop/Obsidian/O_2025")

TTML_WATCH_FOLDER = WATCH_FOLDER
EXTRACT_WATCH_FOLDER = WATCH_FOLDER
AUDIO_TRANSCRIBED_TXT_FOLDER = WATCH_FOLDER
PATH_CONFIG = {
    "WATCH_FOLDER": WATCH_FOLDER,
    "WHISPER_FOLDER": WHISPER_FOLDER,
    "TTML_WATCH_FOLDER": TTML_WATCH_FOLDER,
    "EXTRACT_WATCH_FOLDER": EXTRACT_WATCH_FOLDER,
    "AUDIO_TRANSCRIBED_TXT_FOLDER": AUDIO_TRANSCRIBED_TXT_FOLDER,
    "AUDIO_WATCH_FOLDERS": (
        WATCH_FOLDER,
        WHISPER_FOLDER,
    ),
    "AUDIO_DONE_FOLDER": AUDIO_DONE_FOLDER,
    "PRETEXT_WATCH_FOLDER": WATCH_FOLDER,    # WHISPER_FOLDER, WATCH_FOLDER
    "PREMIUM_WATCH_FOLDER": WHISPER_FOLDER / "Fail" / "p",
    "PRETEXT_DONE_FOLDER": WHISPER_FOLDER / "_p",
    "ARCHIVE_FOLDER": WHISPER_FOLDER / "_p",
    "ORIGINAL_FOLDER": WHISPER_FOLDER / "_p" / "Raw",
    "EXTRACT_FOLDER": WHISPER_FOLDER / "_p" / "Extract",
    "LINK_BACKUP_FOLDER": WHISPER_FOLDER / "_p" / "link_backup",
    "FAIL_FOLDER": WHISPER_FOLDER / "Fail",
    "OBSIDIAN_SYNC_FOLDER": OBSIDIAN_SYNC_FOLDER,
    "X_URL_LIST_FILE": WHISPER_FOLDER / "X" / "X.txt",
    "DOWNLOAD_TARGET_FOLDER": WHISPER_FOLDER / "X",
}

INTERVAL_CONFIG = {
    # folder / file scan intervals
    "PERIODIC_SCAN_SECONDS": 60,          # pretext/extract/premium/torrent backup scan
    "DOWNLOAD_SCAN_SECONDS": 30,          # x.txt / X.txt URL downloader scan
    "AUDIO_IDLE_SCAN_SECONDS": 60,        # audio queue empty -> rescan later
    "TTML_SCAN_SECONDS": 2,               # TTML folder polling
    "WIKILINK_CLEAN_SECONDS": 60,         # main p.py wikilink cleaner

    # queue worker pacing
    "TEXT_QUEUE_IDLE_SECONDS": 0.5,       # pretext/extract/premium queue empty
    "TEXT_QUEUE_LOOP_SECONDS": 0.5,       # sleep after text queue loop
    "FILE_LOCK_RETRY_SECONDS": 1,         # lock miss -> requeue delay

    # readiness / error backoff
    "FILE_READY_STABILITY_SECONDS": 1.0,  # TTML size stable check
    "PIPELINE_ERROR_BACKOFF_SECONDS": 5,  # audio/TTML error sleep
    "SCAN_ERROR_BACKOFF_SECONDS": 60,     # periodic scanner error sleep

    # monitoring only
    "STATUS_LOG_SECONDS": 300,            # orchestrator status log loop

    # LLM/API timing
    "LLM_TIMEOUT_SECONDS": 90,
    "LLM_RETRY_DELAY_SECONDS": 10,
    "LLM_MAX_RETRIES": 2,

    # ytd / downloader network timing
    "X_RESOLVE_TIMEOUT_SECONDS": 20,

    # standalone tool only, if we want it shared too
    "STANDALONE_WIKILINK_CLEAN_SECONDS": 120,
}

CONFIG = {
    **PATH_CONFIG,
    "MODEL_PRETEXT": MODEL_PRETEXT,
    "MODEL_EXTRACT_MATRIX": MODEL_EXTRACT_MATRIX,
    "MODEL_DISTILL": MODEL_DISTILL,
    "PRETEXT_SUFFIX": ".txt",
    "EXTRACT_SUFFIX": ("_p.txt", ".md"),
    "INTERVALS": INTERVAL_CONFIG,
    # 由 orchestration 注入
    "PRETEXT_PROMPT": None,
    "EXTRACT_PROMPT": None,
    "DISTILL_PROMPT": None,
}


os.environ["PYTORCH_ALLOC_CONF"] = "max_split_size_mb:128,backend:native"
os.environ["PYTORCH_NO_CUDA_MEMORY_CACHING"] = "0"


class UTFStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            stream.buffer.write(msg.encode("utf-8"))
            stream.buffer.write(self.terminator.encode("utf-8"))
            self.flush()
        except Exception:
            self.handleError(record)


LOG_DIR = Path(__file__).resolve().parent / "log"
LOG_DIR.mkdir(parents=True, exist_ok=True)


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


CURRENT_CONTEXT: Optional[PipelineContext] = None


class SystemHandles(NamedTuple):
    context: PipelineContext
    threads: Dict[str, threading.Thread]
    observer: Any


def ensure_directories(cfg: Dict[str, Any]) -> None:
    os.makedirs(cfg["ORIGINAL_FOLDER"], exist_ok=True)
    os.makedirs(cfg["AUDIO_DONE_FOLDER"], exist_ok=True)
    os.makedirs(cfg["LINK_BACKUP_FOLDER"], exist_ok=True)
    os.makedirs(cfg["FAIL_FOLDER"], exist_ok=True)


def start_system(cfg: Optional[Dict[str, Any]] = None) -> SystemHandles:
    if cfg is None:
        raise ValueError("Configuration dictionary is required.")

    cfg["PRETEXT_PROMPT"] = read_prompt_file("prompt_pretext.txt")
    cfg["EXTRACT_PROMPT"] = read_prompt_file("prompt_extract.txt")
    cfg["DISTILL_PROMPT"] = read_prompt_file("prompt_distill.txt")

    setup_wikilink_cleaner_logging(logging.getLogger())
    ensure_directories(cfg)

    ctx = create_pipeline_context(cfg)
    global CURRENT_CONTEXT
    CURRENT_CONTEXT = ctx

    (
        pretext_handler,
        pretext_processor,
        extract_handler,
        premium_extract_handler,
    ) = create_pipeline_handlers(ctx)

    threads: Dict[str, threading.Thread] = {
        "TTMLPipeline": threading.Thread(
            target=process_ttml_pipeline,
            args=(ctx,),
            daemon=True,
            name="TTMLPipeline",
        ),
        "TextPipeline-Pretext": threading.Thread(
            target=process_pretext_queue,
            args=(ctx, pretext_processor),
            daemon=True,
            name="TextPipeline-Pretext",
        ),
        "TextPipeline-Extract": threading.Thread(
            target=process_extract_queue,
            args=(ctx, extract_handler),
            daemon=True,
            name="TextPipeline-Extract",
        ),
        "TextPipeline-PremiumExtract": threading.Thread(
            target=process_premium_extract_queue,
            args=(ctx, premium_extract_handler),
            daemon=True,
            name="TextPipeline-PremiumExtract",
        ),
        "AudioPipeline-GPU": threading.Thread(
            target=process_audio_pipeline,
            args=(ctx,),
            daemon=True,
            name="AudioPipeline-GPU",
        ),
        "PeriodicScanner": threading.Thread(
            target=periodic_file_scanner,
            args=(ctx,),
            daemon=True,
            name="PeriodicScanner",
        ),
        "WikilinkCleaner": threading.Thread(
            target=process_wikilink_cleaning,
            args=(ctx,),
            daemon=True,
            name="WikilinkCleaner",
        ),
        "XUrlDownloadPipeline": threading.Thread(
            target=process_x_url_download_pipeline,
            args=(ctx,),
            daemon=True,
            name="XUrlDownloadPipeline",
        ),
    }

    for t in threads.values():
        t.start()

    scan_existing_files(ctx)

    observer = Observer()
    observer.schedule(
        pretext_handler,
        os.fspath(cfg["PRETEXT_WATCH_FOLDER"]),
        recursive=False,
    )
    observer.schedule(
        extract_handler,
        os.fspath(cfg["EXTRACT_WATCH_FOLDER"]),
        recursive=False,
    )
    observer.schedule(
        premium_extract_handler,
        os.fspath(cfg["PREMIUM_WATCH_FOLDER"]),
        recursive=False,
    )
    observer.start()

    return SystemHandles(context=ctx, threads=threads, observer=observer)


def stop_system(handles: SystemHandles) -> None:
    handles.context.shutdown_flag.set()
    try:
        handles.observer.stop()
        handles.observer.join()
    except Exception:
        pass
    finally:
        global CURRENT_CONTEXT
        CURRENT_CONTEXT = None


def system_status() -> Dict[str, Any]:
    if CURRENT_CONTEXT is None:
        raise RuntimeError("System context not initialized.")
    ctx = CURRENT_CONTEXT
    return {
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


def main(cfg: Optional[Dict[str, Any]] = None) -> None:
    resolved_cfg = cfg or CONFIG
    logging.info(
        "Starting: TTML + Text + Audio + WikilinkCleaner + XUrlDownloadPipeline"
    )
    handles = start_system(resolved_cfg)

    logging.info("TTML: Independent subtitle file processing")
    logging.info("Text: Pretext → Extract/Premium Extract")
    logging.info("Download: X.txt URL processing")
    intervals = handles.context.config.get("INTERVALS", {})
    status_log_seconds = intervals.get("STATUS_LOG_SECONDS", 300)

    try:
        while True:
            ctx = CURRENT_CONTEXT
            if ctx is None:
                raise RuntimeError("System context not initialized.")
            total_queues = (
                ctx.pretext_queue.qsize()
                + ctx.extract_queue.qsize()
                + ctx.premium_extract_queue.qsize()
            )
            if total_queues > 5:
                logging.info(
                    "System Status - Text queues: Pretext: %d, Extract: %d, Premium Extract: %d",
                    ctx.pretext_queue.qsize(),
                    ctx.extract_queue.qsize(),
                    ctx.premium_extract_queue.qsize(),
                )

            time.sleep(status_log_seconds)
    except KeyboardInterrupt:
        stop_system(handles)
        logging.info("4-pipeline independent parallel system stopped")


if __name__ == "__main__":
    main()
