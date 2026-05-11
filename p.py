# p.py
from __future__ import annotations

import logging
import threading
from pathlib import Path
from w.helper_files import configure_logging
from w.p_pipelines import (
    create_runtime,
    create_extract_processors,
    process_extract_queue,
    process_premium_extract_queue,
    process_pretext_queue,
)
from w.p_wiki import process_wikilink_cleaning
from w.p_audio import process_audio_pipeline
from w.p_torrent import process_torrent_pipeline
from w.p_ttml import process_ttml_pipeline
from w.p_ytd import process_ytd_pipeline

BASE_DIR = Path(__file__).resolve().parent
WHISPER_FOLDER = Path("/desktop/Sync/Whisper")
WATCH_FOLDER = Path("/desktop")

CONFIG = {
    "MODEL_PRETEXT": "gpt-4.1-mini",
    "MODEL_DISTILL": "o3",
    "MODEL_EXTRACT_MATRIX": {
        "EXTRACT_WATCH_FOLDER": [
            "gpt-5.4-mini",
            "grok-4.20-non-reasoning",  # grok-4.3, grok-4-1-fast-non-reasoning
            "gemini-3.1-flash-lite-preview",  # gemini-3.1-flash-lite-preview, gemini-3.1-pro-preview"
        ],
        "PREMIUM_WATCH_FOLDER": [
            "gpt-5.4",
        ],
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
    "YTD_LIST_FILE": WHISPER_FOLDER / "X" / "X.txt",
    "DOWNLOAD_TARGET_FOLDER": WHISPER_FOLDER / "X",
    "LOG_DIR": BASE_DIR / "data" / "logs",
    "PRETEXT_SUFFIX": ".txt",
    "EXTRACT_SUFFIX": ("_p.txt", ".md"),
    "PRETEXT_PROMPT": (BASE_DIR / "prompt" / "prompt_pretext.txt").read_text(encoding="utf-8").strip(),
    "EXTRACT_PROMPT": (BASE_DIR / "prompt" / "prompt_extract.txt").read_text(encoding="utf-8").strip(),
    "DISTILL_PROMPT": (BASE_DIR / "prompt" / "prompt_distill.txt").read_text(encoding="utf-8").strip(),
}

def start_runtime(ctx) -> dict[str, threading.Thread]:
    extract_processor, premium_extract_processor = create_extract_processors(ctx)
    threads: dict[str, threading.Thread] = {}

    thread_specs = [
        (ctx.config["PIPELINES"]["TORRENT"], "TorrentPipeline", process_torrent_pipeline, (ctx,)),
        (ctx.config["PIPELINES"]["TTML"], "TTMLPipeline", process_ttml_pipeline, (ctx,)),
        (ctx.config["PIPELINES"]["PRETEXT"], "TextPipeline-Pretext", process_pretext_queue, (ctx,)),
        (ctx.config["PIPELINES"]["EXTRACT"], "TextPipeline-Extract", process_extract_queue, (ctx, extract_processor)),
        (ctx.config["PIPELINES"]["EXTRACT"], "TextPipeline-PremiumExtract", process_premium_extract_queue, (ctx, premium_extract_processor)),
        (ctx.config["PIPELINES"]["AUDIO"], "AudioPipeline-GPU", process_audio_pipeline, (ctx,)),
        (ctx.config["PIPELINES"]["WIKI"], "WikilinkCleaner", process_wikilink_cleaning, (ctx,)),
        (ctx.config["PIPELINES"]["YTD"], "YTDPipeline", process_ytd_pipeline, (ctx,)),
    ]

    for enabled, name, target, args in thread_specs:
        if enabled:
            thread = threading.Thread(target=target, args=args, daemon=True, name=name)
            threads[name] = thread
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
