#!/usr/bin/env python3
"""Step 6 coeffs_to_filing Move chart debug outputs into a model-named folder."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from path_config import load_chart_runtime

SERIES_RE = re.compile(r"^(?P<model>.+)_(?P<series>FI[A-Z])(?:_|$)")


def infer_model_name(debug_dir: Path) -> str:
    model_names: set[str] = set()
    for p in debug_dir.iterdir():
        if not p.is_file():
            continue
        m = SERIES_RE.match(p.stem)
        if m:
            model_names.add(m.group("model"))

    if not model_names:
        raise RuntimeError("No debug files with pattern XXXXX_FI?.* found")
    if len(model_names) > 1:
        raise RuntimeError(f"Multiple model names found: {sorted(model_names)}")
    return next(iter(model_names))


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    raw_dir, debug_dir , _ = load_chart_runtime(base_dir)
    debug_dir = raw_dir / "debug"
    if not debug_dir.exists():
        raise RuntimeError(f"Debug directory not found: {debug_dir}")

    model_name = infer_model_name(debug_dir)
    target_dir = raw_dir / model_name
    target_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for p in debug_dir.iterdir():
        if not p.is_file():
            continue
        shutil.move(str(p), str(target_dir / p.name))
        moved += 1

    config_path = raw_dir / "chart_config.json"
    if config_path.exists():
        shutil.move(str(config_path), str(target_dir / f"Z-{model_name}.json"))

    print(f"[OK] folder={model_name} moved={moved}")


if __name__ == "__main__":
    main()
