#!/usr/bin/env python3
# step 1 png_to_mask.py (Optimized Version)
import cv2
import numpy as np
from pathlib import Path
from path_config import load_chart_runtime

# ==========================
# PATH CONFIG
# ==========================

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "chart_config.json"
RAW_DIR, DEBUG_DIR, runtime_config = load_chart_runtime(BASE_DIR, CONFIG_PATH)

CHART_CONFIG = runtime_config["charts"]
FILENAME_TO_CONFIG = {cfg["filename"]: cfg for cfg in CHART_CONFIG.values()}

# ==========================
# CORE ALGORITHM
# ==========================

def extract_curve_optimized(plot_img):
    """
    基於強度（Otsu）與結構特徵（弧長）提取曲線，避免誤刪平直數據。
    """
    # 1. 灰度與對比度強化
    gray = cv2.cvtColor(plot_img, cv2.COLOR_BGR2GRAY)
    
    # 2. Otsu 二值化：利用「曲線比網格黑」的特性自動分離
    # 使用 THRESH_BINARY_INV 將深色曲線轉為白色前景
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 3. 形態學開運算：使用 3x3 等向性核移除殘留的細小網格線 (1px)
    kernel = np.ones((3, 3), np.uint8)
    refined = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    # 4. 提取最大輪廓：使用弧長 (Arc Length) 而非面積，確保選取的是「長曲線」
    contours, _ = cv2.findContours(refined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    mask = np.zeros_like(refined)
    if contours:
        # 尋找弧長最長的輪廓（曲線通常橫跨圖表，長度特徵最明顯）
        best_cnt = max(contours, key=lambda c: cv2.arcLength(c, False))
        cv2.drawContours(mask, [best_cnt], -1, 255, thickness=cv2.FILLED)
    
    return refined, mask

# ==========================
# PIPELINE
# ==========================

def process_image(img_path: Path):
    cfg = FILENAME_TO_CONFIG.get(img_path.name)
    if not cfg or cfg["plot_bbox"] == [0, 0, 0, 0]:
        print(f"[SKIP] No valid config for: {img_path.name}")
        return

    img = cv2.imread(str(img_path))
    if img is None: return

    # 1. Crop
    x0, y0, x1, y1 = cfg["plot_bbox"]
    plot = img[y0:y1, x0:x1]
    
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stem = img_path.stem

    # 2. Process
    # grid_removed 對應二值化後但尚未篩選輪廓的狀態，用於調試
    binary_grid_removed, mask = extract_curve_optimized(plot)

    # 3. Save Debug Outputs
    cv2.imwrite(str(DEBUG_DIR / f"{stem}_plot_crop.png"), plot)
    cv2.imwrite(str(DEBUG_DIR / f"{stem}_grid_removed.png"), binary_grid_removed)
    cv2.imwrite(str(DEBUG_DIR / f"{stem}_mask_curve.png"), mask)

    print(f"[OK] {img_path.name}")

def main():
    if not RAW_DIR.exists(): return
    png_files = sorted(RAW_DIR.glob("*.png"))
    for img_path in png_files:
        process_image(img_path)
    print("All done.")

if __name__ == "__main__":
    main()