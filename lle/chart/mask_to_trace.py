#!/usr/bin/env python3 
# step 2 mask_to_trace.py (Corrected JSON Output)
import json
import numpy as np
import cv2
from pathlib import Path
from path_config import load_chart_runtime

# ==========================
# PATH CONFIG
# ==========================
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR, DEBUG_DIR, _ = load_chart_runtime(BASE_DIR)

# ==========================
# TRACE CORE
# ==========================

def trace_curve_points(mask: np.ndarray, plot_img: np.ndarray):
    H, W = mask.shape[:2]
    # 使用輕微的高斯模糊消除噪聲，讓波谷更穩定
    gray = cv2.GaussianBlur(cv2.cvtColor(plot_img, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    
    ys_raw = np.full(W, np.nan, dtype=np.float32)

    for x in range(W):
        y_indices = np.where(mask[:, x] > 0)[0]
        if y_indices.size > 0:
            col_gray = gray[y_indices, x]
            min_val = np.min(col_gray)
            darkest_pts = y_indices[col_gray == min_val]
            ys_raw[x] = float(np.median(darkest_pts))

    valid = np.isfinite(ys_raw)
    # 這裡的 info 包含了 trace_to_fit.py 需要的 width 和 height
    info = {
        "width": int(W),
        "height": int(H),
        "valid_points": int(valid.sum()),
        "x_coverage": float(valid.mean()) if W > 0 else 0
    }
    return np.arange(W, dtype=np.float32), ys_raw, info

def draw_trace_overlay(plot_img, xp, yp):
    out = plot_img.copy()
    mask_valid = np.isfinite(yp)
    if not np.any(mask_valid): return out
    for i in range(len(xp)):
        if mask_valid[i]:
            cv2.circle(out, (int(xp[i]), int(yp[i])), 0, (0, 0, 255), -1)
    return out

# ==========================
# PIPELINE
# ==========================

def process_mask(mask_path: Path):
    stem = mask_path.stem.replace("_mask_curve", "")
    plot_path = DEBUG_DIR / f"{stem}_plot_crop.png"
    
    if not plot_path.exists(): return

    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    plot = cv2.imread(str(plot_path))
    if mask is None or plot is None: return

    xp, yp_raw, info = trace_curve_points(mask, plot)

    # 保存 Overlay
    overlay = draw_trace_overlay(plot, xp, yp_raw)
    cv2.imwrite(str(DEBUG_DIR / f"{stem}_trace_overlay.png"), overlay)

    # --- 修正後的 JSON 輸出部分 ---
    out_json = {
        "stem": stem,
        "trace_info": info,  # <--- 補回這個字段，解決 KeyError
        "xp": xp.tolist(),
        "yp_raw": yp_raw.tolist()
    }
    # ----------------------------

    with open(DEBUG_DIR / f"{stem}_curve_points_px.json", "w") as f:
        json.dump(out_json, f, indent=2)

    print(f"[OK] {stem} - {info['valid_points']} pts")

def main():
    mask_files = sorted(DEBUG_DIR.glob("*_mask_curve.png"))
    for mf in mask_files:
        process_mask(mf)
    print("Mask to Trace complete.")

if __name__ == "__main__":
    main()