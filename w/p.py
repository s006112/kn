from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from queue import Queue

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from w.helper_files import configure_logging
from w.p_audio import process_audio_pipeline
from w.p_txt import process_text_pipeline
from w.p_torrent import process_torrent_pipeline
from w.p_ttml import process_ttml_pipeline
from w.p_wiki import process_wikilink_cleaning
from w.p_ytd import process_ytd_pipeline

WATCH_FOLDER = Path("/desktop")
WHISPER_FOLDER = Path("/desktop/Sync/Whisper")

CONFIG = {
    "MODEL_PRETEXT": "gpt-5.4-mini",
    "MODEL_DISTILL": "o3",
    "MODEL_EXTRACT_MATRIX": {
        "EXTRACT_WATCH_FOLDER": [
            "grok-4-1-fast-reasoning",  # grok-4.3, 
            "gemini-3.1-pro-preview",  # gemini-3.1-flash-lite-preview, gemini-3.1-pro-preview"
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
    "AUDIO_WATCH_FOLDERS": (WATCH_FOLDER, WHISPER_FOLDER),
    "AUDIO_DONE_FOLDER": Path("/desktop/YT1"),
    "PRETEXT_WATCH_FOLDER": WATCH_FOLDER,
    "PRETEXT_DONE_FOLDER": WHISPER_FOLDER / "_p",
    "ORIGINAL_FOLDER": WHISPER_FOLDER / "_p" / "Raw",
    "EXTRACT_FOLDER": WHISPER_FOLDER / "_p" / "Extract",
    "LINK_BACKUP_FOLDER": WHISPER_FOLDER / "_p" / "link_backup",
    "OBSIDIAN_SYNC_FOLDER": Path("/desktop/Obsidian/O_2025"),
    "YTD_LIST_FILE": WHISPER_FOLDER / "X" / "X.txt",
    "DOWNLOAD_TARGET_FOLDER": WHISPER_FOLDER / "X",
    "LOG_DIR": ROOT_DIR / "data" / "logs",
    "PRETEXT_SUFFIX": ".txt",
    "EXTRACT_SUFFIX": "_p.txt",
    "PRETEXT_PROMPT": (ROOT_DIR / "prompt" / "prompt_pretext.txt").read_text(encoding="utf-8").strip(),
    "EXTRACT_PROMPT": (ROOT_DIR / "prompt" / "prompt_extract.txt").read_text(encoding="utf-8").strip(),
    "DISTILL_PROMPT": (ROOT_DIR / "prompt" / "prompt_distill.txt").read_text(encoding="utf-8").strip(),
    "CLASSIFIER_PROMPT": (ROOT_DIR / "prompt" / "prompt_core_classifier.txt").read_text(encoding="utf-8").strip(),
}


def start_runtime(config) -> tuple[dict[str, threading.Thread], threading.Event]:
    audio_queue = Queue()
    ttml_queue = Queue()
    audio_processing_lock = threading.Lock()
    wikilink_cleaning_stats = {"last_run": None, "cycle_count": 0}
    shutdown_flag = threading.Event()

    text_threads = process_text_pipeline(config, shutdown_flag)
    threads = {
        name: threading.Thread(target=target, args=args, daemon=True, name=name)
        for enabled, name, target, args in [
            (config["PIPELINES"]["TORRENT"], "TorrentPipeline", process_torrent_pipeline, (config, shutdown_flag)),
            (config["PIPELINES"]["TTML"], "TTMLPipeline", process_ttml_pipeline, (config, ttml_queue, shutdown_flag)),
            (config["PIPELINES"]["AUDIO"], "AudioPipeline-GPU", process_audio_pipeline, (config, audio_queue, audio_processing_lock, shutdown_flag)),
            (config["PIPELINES"]["WIKI"], "WikilinkCleaner", process_wikilink_cleaning, (config, shutdown_flag, wikilink_cleaning_stats)),
            (config["PIPELINES"]["YTD"], "YTDPipeline", process_ytd_pipeline, (config, shutdown_flag)),
        ]
        if enabled
    }

    for thread in threads.values():
        thread.start()

    return {**text_threads, **threads}, shutdown_flag


def main() -> None:
    configure_logging(CONFIG["LOG_DIR"])

    _, shutdown_flag = start_runtime(CONFIG)

    logging.info("Enabled: %s", ", ".join(key for key, enabled in CONFIG["PIPELINES"].items() if enabled) or "none")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_flag.set()


if __name__ == "__main__":
    main()
