"""
入口 shell：负责环境变量、日志初始化，并委托 p_orchestrator.main()。
整个执行链为 p.py → p_orchestrator.py → p_pipelines.py/p_context.py。
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# -------------------------
# 路径与运行配置
# -------------------------
WATCH_FOLDER = Path("/desktop/Sync/Whisper")
PATH_CONFIG = {
    "WATCH_FOLDER": WATCH_FOLDER,
    "AUDIO_WATCH_FOLDERS": (
        WATCH_FOLDER,
        Path("/desktop/"),
    ),
    "AUDIO_DONE_FOLDER": Path("/desktop/YT1"),
    "PRETEXT_WATCH_FOLDER": WATCH_FOLDER,
    "PREMIUM_WATCH_FOLDER": Path("/desktop"),
    # PRETEXT_DONE_FOLDER = WATCH_FOLDER / "_p"
    "PRETEXT_DONE_FOLDER": Path("/desktop"),
    "ARCHIVE_FOLDER": WATCH_FOLDER / "_p",
    "ORIGINAL_FOLDER": WATCH_FOLDER / "_p" / "Raw",
    "EXTRACT_FOLDER": WATCH_FOLDER / "_p" / "Extract",
    "LINK_BACKUP_FOLDER": WATCH_FOLDER / "_p" / "link_backup",
    "FAIL_FOLDER": WATCH_FOLDER / "Fail",
    "OBSIDIAN_SYNC_FOLDER": Path("/desktop/Obsidian/O_2025"),
}

# sonar, sonar-pro, sonar-reasoning, sonar-reasoning-pro
# gemini-2.0-flash, gemini-2.5-flash, gemini-2.5-pro, gemini-3-pro-preview, 
# gpt-5-mini, gpt-5-nano, gpt-4.1-mini, gpt-4.1-nano, gpt-4o-mini, o1-mini, o3-mini, o4-mini, codex-mini-latest
# gpt-5.1, gpt-5, gpt-5-chat-latest, gpt-4.1, gpt-4o, o1, o3,
MODEL_PRETEXT = "gpt-4.1-mini"
MODEL_EXTRACT_MATRIX = {
    "WATCH_FOLDER": [
        "sonar-reasoning-pro",
        "gemini-3-pro-preview",
        "gpt-5.1",
    ],
    "PREMIUM_WATCH_FOLDER": [
        "o3",
    ],
}

RETRY_CONFIG = {
    "MAX_RETRIES": 1,
    "RETRY_DELAY": 5,  # seconds
}

CONFIG = {
    **PATH_CONFIG,
    "MODEL_PRETEXT": MODEL_PRETEXT,
    "MODEL_EXTRACT_MATRIX": MODEL_EXTRACT_MATRIX,
    **RETRY_CONFIG,
    # 这两项由 orchestration 注入
    "PRETEXT_PROMPT": None,
    "EXTRACT_PROMPT": None,
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


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        RotatingFileHandler(
            "script.log", maxBytes=1 * 1024 * 1024, backupCount=2, encoding="utf-8"
        ),
        UTFStreamHandler(sys.stdout),
    ],
)


from p_orchestrator import (  # noqa: E402
    main as _orchestrator_main,
    start_system as _start_system,
    stop_system,
    system_status,
)


def main(cfg=None) -> None:
    _orchestrator_main(cfg or CONFIG)


def start_system(cfg=None):
    return _start_system(cfg or CONFIG)


if __name__ == "__main__":
    main()
