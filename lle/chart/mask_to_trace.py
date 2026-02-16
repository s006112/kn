# mask_to_trace.py

#!/usr/bin/env python3
import json
import numpy as np
import cv2
from pathlib import Path
from typing import Dict, Any

# ==========================
# PATH CONFIG
# ==========================
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR  = (BASE_DIR / "../../data/chart/raw").resolve()

DEBUG_DIR = RAW_DIR / "debug"

# ==========================
# TRACE CORE
# ==========================

def trace_curve_points(mask: np.ndarray):
    H, W = mask.shape[:2]
    fg = (mask > 0)

    ys_med = np.full(W, np.nan, dtype=np.float32)
    count_per_x = np.zeros(W, dtype=np.int32)

    for x in range(W):
        ys = np.where(fg[:, x])[0]
        count_per_x[x] = ys.size
        if ys.size:
            ys_med[x] = float(np.median(ys))

    valid = np.isfinite(ys_med)
    valid_idx = np.where(valid)[0]

    info = {
        "width": int(W),
        "height": int(H),
        "x_coverage": float(valid.mean()),
        "largest_gap_px": int(np.diff(valid_idx).max()) if valid_idx.size > 1 else 0,
    }

    if valid_idx.size < 20:
        info["status"] = "too_few_points"
        return np.array([]), np.array([]), info

    xs = np.arange(W, dtype=np.float32)
    ys_fill = ys_med.copy()
    ys_fill[~valid] = np.interp(xs[~valid], xs[valid], ys_med[valid])

    info["status"] = "ok"
    return xs, ys_fill, info


def draw_trace_overlay(plot_img, xp, yp):
    out = plot_img.copy()
    if xp.size == 0:
        return out

    pts = np.stack([xp, yp], axis=1).round().astype(np.int32)

    for i in range(1, pts.shape[0]):
        cv2.line(out, tuple(pts[i-1]), tuple(pts[i]), (0,0,255), 1)

    return out


# ==========================
# PIPELINE
# ==========================

def process_mask(mask_path: Path):
    stem = mask_path.stem.replace("_mask_curve", "")

    plot_path = DEBUG_DIR / f"{stem}_plot_crop.png"
    if not plot_path.exists():
        print(f"[SKIP] No plot_crop for {stem}")
        return

    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    plot = cv2.imread(str(plot_path))

    xp, yp, info = trace_curve_points(mask)

    overlay = draw_trace_overlay(plot, xp, yp)
    cv2.imwrite(str(DEBUG_DIR / f"{stem}_trace_overlay.png"), overlay)

    out_json = {
        "stem": stem,
        "trace_info": info,
        "xp": xp.tolist(),
        "yp": yp.tolist(),
    }

    with open(DEBUG_DIR / f"{stem}_curve_points_px.json", "w") as f:
        json.dump(out_json, f, indent=2)

    print(f"[OK] {stem}")


def main():
    if not DEBUG_DIR.exists():
        raise RuntimeError("Run png_to_mask.py first")

    mask_files = sorted(DEBUG_DIR.glob("*_mask_curve.png"))

    if not mask_files:
        print("No mask files found.")
        return

    print(f"Found {len(mask_files)} mask files.")

    for mask_path in mask_files:
        process_mask(mask_path)

    print("Trace done.")


if __name__ == "__main__":
    main()

