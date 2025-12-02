#!/usr/bin/env python3
"""
ali_state_store.py

- 以 JSON 檔記錄每個 uid 的處理狀態
- has_processed(uid) 只在 status == "success" 時回 True
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional

from utils_config import load_env, configure_logging  # type: ignore  :contentReference[oaicite:0]{index=0}


class AliStateStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        load_env()
        self.logger = configure_logging("ali_state_store")
        default_path = Path(".state") / "ali_state.json"
        self.path = path or Path(os.getenv("ALI_STATE_PATH", default_path))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Dict[str, Dict[str, object]]] = {"processed": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            text = self.path.read_text("utf-8")
            data = json.loads(text)
            if isinstance(data, dict) and "processed" in data:
                self._data = data  # type: ignore[assignment]
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Failed to load state file %s: %s", self.path, exc)

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def has_processed(self, uid: int) -> bool:
        info = self._data["processed"].get(str(uid))
        return bool(info and info.get("status") == "success")

    def mark_success(self, uid: int) -> None:
        self._data["processed"][str(uid)] = {
            "status": "success",
            "processed_at": time.time(),
        }
        self._save()

    def mark_failure(self, uid: int, error: str) -> None:
        self._data["processed"][str(uid)] = {
            "status": "failed",
            "processed_at": time.time(),
            "error": (error or "")[:500],
        }
        self._save()
