import logging
import os
import threading
import time
from typing import Any, Dict, NamedTuple, Optional

from watchdog.observers import Observer
from p_context import PipelineContext, create_pipeline_context
from p_pipelines import (
    create_pipeline_handlers,
    periodic_file_scanner,
    process_audio_pipeline,
    process_extract_queue,
    process_premium_extract_queue,
    process_pretext_queue,
    process_ttml_pipeline,
    process_x_url_download_pipeline,
    process_wikilink_cleaning,
    scan_existing_files,
)
from utils_unlink import setup_wikilink_cleaner_logging
from utils_files import read_prompt_file


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
    logging.info(
        "Starting: TTML + Text + Audio + WikilinkCleaner + XUrlDownloadPipeline"
    )
    handles = start_system(cfg)

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
