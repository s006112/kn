#!/usr/bin/env python3
"""Run chart extraction pipeline end-to-end.

Order:
1) png_to_mask.py
2) mask_to_trace.py
3) trace_to_fit.py
4) fit_to_coeffs.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    steps = [
        "png_to_mask.py",
        "mask_to_trace.py",
        "trace_to_fit.py",
        "fit_to_coeffs.py",
    ]

    for step in steps:
        step_path = base_dir / step
        print(f"[RUN] {step}")
        result = subprocess.run([sys.executable, str(step_path)], cwd=str(base_dir))
        if result.returncode != 0:
            print(f"[FAIL] {step} exit={result.returncode}")
            return result.returncode

    print("[OK] Pipeline complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
