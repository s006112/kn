#!/usr/bin/env python3 
# step 2 mask_to_trace.py (First Principle: Darkest Core Tracking)
import json
import numpy as np
import cv2
from pathlib import Path
from path_config import load_chart_runtime

# ==========================
# PATH CONFIG
# ==========================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "chart_config.json"
RAW_DIR, DEBUG_DIR, _ = load_chart_runtime(BASE_DIR, CONFIG_PATH)

# ==========================
# TRACE CORE
# ==========================

def trace_curve_points(mask: np.ndarray, plot_img: np.ndarray):
    """
    第一性原理：追蹤每一列中最黑的核心點。
    無視邊緣噪聲，直接鎖定數值上的「波谷」。
    """
    H, W = mask.shape[:2]
    # 使用輕微的高斯模糊 (1px) 消除單個像素的隨機噪聲，讓波谷更平滑
    gray = cv2.GaussianBlur(cv2.cvtColor(plot_img, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    
    ys_raw = np.full(W, np.nan, dtype=np.float32)

    for x in range(W):
        # 找到當前列 Mask 覆蓋的 Y 座標
        y_indices = np.where(mask[:, x] > 0)[0]
        
        if y_indices.size > 0:
            col_gray = gray[y_indices, x]
            
            # 找到該列中最黑的數值
            min_val = np.min(col_gray)
            
            # 找到所有達到最黑標準的像素位置
            # 這樣即使線條很粗，我們也能精確找到「最黑那一塊」的中點
            darkest_pts = y_indices[col_gray == min_val]
            
            # 取最黑區域的中位數作為中心
            ys_raw[x] = float(np.median(darkest_pts))

    valid = np.isfinite(ys_raw)
    info = {
        "width": int(W),
        "height": int(H),
        "valid_points": int(valid.sum()),
        "x_coverage": float(valid.mean()) if W > 0 else 0
    }
    return np.arange(W, dtype=np.float32), ys_raw, info

def draw_trace_overlay(plot_img, xp, yp):
    out = plot_img.copy()
    # 過濾出有效的點
    mask_valid = np.isfinite(yp)
    if not np.any(mask_valid): return out

    # 繪製紅點 trace，厚度設為 1 以觀察精確度
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

    # 保存 Overlay 調試
    overlay = draw_trace_overlay(plot, xp, yp_raw)
    cv2.imwrite(str(DEBUG_DIR / f"{stem}_trace_overlay.png"), overlay)

    # 保存數據
    out_json = {
        "stem": stem,
        "xp": xp.tolist(),
        "yp_raw": yp_raw.tolist()
    }
    with open(DEBUG_DIR / f"{stem}_curve_points_px.json", "w") as f:
        json.dump(out_json, f, indent=2)

    print(f"[OK] {stem} - {info['valid_points']} pts")

def main():
    mask_files = sorted(DEBUG_DIR.glob("*_mask_curve.png"))
    for mf in mask_files:
        process_mask(mf)
    print("Optimization complete.")

if __name__ == "__main__":
    main()