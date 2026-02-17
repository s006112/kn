#!/usr/bin/env python3
"""path_config.py Shared path resolution for chart pipeline steps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

def _resolve(path_value: str, base: Path) -> Path:
    p = Path(path_value).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (base / p).resolve()


def load_chart_runtime(base_dir: Path) -> Tuple[Path, Path, Dict[str, Any]]:
    """Resolve RAW_DIR and DEBUG_DIR from config/defaults.

    Priority:
    1) config.paths.raw_dir / config.paths.debug_dir
    2) defaults: ../../data/chart/raw and RAW_DIR/debug

    Relative `raw_dir` is resolved from base_dir.
    Relative `debug_dir` is resolved from RAW_DIR.
    """

    config_path = base_dir / "chart_config.json"
    config: Dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            config = json.load(f)

    paths_cfg = config.get("paths", {}) if isinstance(config, dict) else {}

    raw_cfg = paths_cfg.get("raw_dir")
    if raw_cfg:
        raw_dir = _resolve(str(raw_cfg), base_dir)
    else:
        raw_dir = (base_dir / "../../data/chart/raw").resolve()

    debug_cfg = paths_cfg.get("debug_dir")
    if debug_cfg:
        debug_dir = _resolve(str(debug_cfg), raw_dir)
    else:
        debug_dir = (raw_dir / "debug").resolve()

    return raw_dir, debug_dir, config
