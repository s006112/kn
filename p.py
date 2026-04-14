import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Make whisper modules importable when running from repo root.
WHISPER_DIR = Path(__file__).resolve().parent / "whisper"
sys.path.insert(0, os.fspath(WHISPER_DIR))

import p_orchestrator  # type: ignore[reportMissingImports]

# sonar $1, sonar-pro $15, sonar-reasoning-pro $8
# gemini-2.5-flash $2.5, gemini-2.5-pro $10, gemini-3.1-flash-lite-preview $1.5, gemini-3-pro-preview $12, 
# gpt-5-mini, gpt-5-nano, gpt-4.1-mini, gpt-4.1-nano, gpt-4o-mini, o1-mini, o3-mini, o4-mini,
# gpt-5.4 $15, gpt-5.2 $14, gpt-5.1 $10, gpt-4.1 $8, gpt-4o, o1 $60, o3 $8,
# grok-4-1-fast-reasoning $0.2, grok-4-1-fast-non-reasoning $0.2, grok-4.20-0309-non-reasoning $2.0
MODEL_PRETEXT = "gpt-4.1-mini"
#MODEL_DISTILL = "grok-4-1-fast-reasoning"
MODEL_DISTILL = "o3"
MODEL_EXTRACT_MATRIX = {
    "EXTRACT_WATCH_FOLDER": [
        #"sonar-reasoning-pro",
        "grok-4.20-0309-non-reasoning",
        "gemini-3-pro-preview",
        "gpt-5.4-mini",
        #"gpt-5.4"
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
}

RETRY_CONFIG = {
    "MAX_RETRIES": 1,
    "RETRY_DELAY": 5,  # seconds
}

CONFIG = {
    **PATH_CONFIG,
    "MODEL_PRETEXT": MODEL_PRETEXT,
    "MODEL_EXTRACT_MATRIX": MODEL_EXTRACT_MATRIX,
    "MODEL_DISTILL": MODEL_DISTILL,
    "PRETEXT_SUFFIX": ".txt",
    "EXTRACT_SUFFIX": ("_p.txt", ".md"),
    **RETRY_CONFIG,
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


def main(cfg=None) -> None:
    """
    Responsibility:
    Initialize runtime configuration and logging, then delegate to `p_orchestrator.main()` with the resolved configuration.

    Pipelines:
    - config -> logging -> orchestrator -> pipelines/workers -> tasks

    Invariants:
    - `CONFIG` remains a dict containing resolved path and model settings used by `main()`.
    - `main()` always delegates to `p_orchestrator.main()` with a non-None config.

    Out of scope:
    - Orchestrator lifecycle control beyond delegating to `p_orchestrator`.
    - Pipeline worker execution or watchdog management.
    """
    p_orchestrator.main(cfg or CONFIG)


if __name__ == "__main__":
    main()
