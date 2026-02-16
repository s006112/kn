#!/usr/bin/env python3
"""Shared path resolution for chart pipeline steps."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

CHART_RAW_DIR_ENV = "CHART_RAW_DIR"
CHART_DEBUG_DIR_ENV = "CHART_DEBUG_DIR"


def _resolve(path_value: str, base: Path) -> Path:
    p = Path(path_value).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (base / p).resolve()


def load_chart_runtime(base_dir: Path, config_path: Path) -> Tuple[Path, Path, Dict[str, Any]]:
    """Resolve RAW_DIR and DEBUG_DIR from env/config with stable defaults.

    Priority:
    1) env: CHART_RAW_DIR / CHART_DEBUG_DIR
    2) config.paths.raw_dir / config.paths.debug_dir
    3) defaults: ../../data/chart/raw and RAW_DIR/debug

    Relative `raw_dir` is resolved from base_dir.
    Relative `debug_dir` is resolved from RAW_DIR.
    """

    config: Dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            config = json.load(f)

    paths_cfg = config.get("paths", {}) if isinstance(config, dict) else {}

    raw_env = os.getenv(CHART_RAW_DIR_ENV)
    raw_cfg = paths_cfg.get("raw_dir")
    if raw_env:
        raw_dir = _resolve(raw_env, base_dir)
    elif raw_cfg:
        raw_dir = _resolve(str(raw_cfg), base_dir)
    else:
        raw_dir = (base_dir / "../../data/chart/raw").resolve()

    debug_env = os.getenv(CHART_DEBUG_DIR_ENV)
    debug_cfg = paths_cfg.get("debug_dir")
    if debug_env:
        debug_dir = _resolve(debug_env, raw_dir)
    elif debug_cfg:
        debug_dir = _resolve(str(debug_cfg), raw_dir)
    else:
        debug_dir = (raw_dir / "debug").resolve()

    return raw_dir, debug_dir, config
