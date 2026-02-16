#!/usr/bin/env python3 
# step 2 mask_to_trace.py
import json
import numpy as np
import cv2
from pathlib import Path

# ==========================
# PATH CONFIG
# ==========================
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR  = (BASE_DIR / "../../data/chart/raw").resolve()
DEBUG_DIR = RAW_DIR / "debug"

# ==========================
# TRACE CORE (RAW ONLY)
# ==========================

def trace_curve_points(mask: np.ndarray):
    """
    Extract raw median y for each x column.
    Missing columns remain NaN.
    """

    H, W = mask.shape[:2]
    fg = mask > 0

    ys_raw = np.full(W, np.nan, dtype=np.float32)

    for x in range(W):
        ys = np.where(fg[:, x])[0]
        if ys.size:
            ys_raw[x] = float(np.median(ys))

    valid = np.isfinite(ys_raw)

    info = {
        "width": int(W),
        "height": int(H),
        "x_coverage": float(valid.mean()),
        "valid_points": int(valid.sum()),
    }

    return np.arange(W, dtype=np.float32), ys_raw, info


def draw_trace_overlay(plot_img, xp, yp):
    """
    Draw only valid segments.
    """
    out = plot_img.copy()

    valid = np.isfinite(yp)
    idx = np.where(valid)[0]

    if idx.size < 2:
        return out

    pts = np.stack([xp[idx], yp[idx]], axis=1).round().astype(np.int32)

    for i in range(1, pts.shape[0]):
        cv2.line(out, tuple(pts[i-1]), tuple(pts[i]), (0, 0, 255), 1)

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

    xp, yp_raw, info = trace_curve_points(mask)

    overlay = draw_trace_overlay(plot, xp, yp_raw)
    cv2.imwrite(str(DEBUG_DIR / f"{stem}_trace_overlay.png"), overlay)

    out_json = {
        "stem": stem,
        "trace_info": info,
        "xp": xp.tolist(),
        "yp_raw": yp_raw.tolist(),   # ← 只输出 raw
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
