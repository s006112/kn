#!/usr/bin/env python3
"""Run chart extraction pipeline end-to-end.

Order:
0) png_to_chart_config.py
1) png_to_mask.py
2) mask_to_trace.py
3) trace_to_fit.py
4) fit_to_coeffs.py
5) coeffs_to_sqlite.py (external, can be run separately for multiple models)
6) coeffs_to_filing.py (external, can be run separately after step 5)

Discipline:
- Always clear debug directory before running.
- Abort immediately on any step failure.
"""

from __future__ import annotations

import subprocess
import sys
import shutil
from pathlib import Path
from path_config import load_chart_runtime


def clear_debug_dir(base_dir: Path) -> None:
    """
    Remove debug directory completely to avoid stale intermediate state.
    """
    _, debug_dir, _ = load_chart_runtime(base_dir)

    if debug_dir.exists():
        print(f"[CLEAN] Removing debug directory: {debug_dir}")
        shutil.rmtree(debug_dir)

    debug_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INIT] Fresh debug directory created.")


def run_step(base_dir: Path, step: str) -> int:
    step_path = base_dir / step
    print(f"[RUN] {step}")
    result = subprocess.run(
        [sys.executable, str(step_path)],
        cwd=str(base_dir)
    )
    if result.returncode != 0:
        print(f"[FAIL] {step} exit={result.returncode}")
    return result.returncode


def main() -> int:
    base_dir = Path(__file__).resolve().parent

    # 1️⃣ 清空 debug
    clear_debug_dir(base_dir)

    # 2️⃣ 顺序执行
    steps = [
        "png_to_chart_config.py",
        "png_to_mask.py",
        "mask_to_trace.py",
        "trace_to_fit.py",
        "fit_to_coeffs.py",
    ]

    for step in steps:
        rc = run_step(base_dir, step)
        if rc != 0:
            return rc

    print("[OK] Pipeline complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
