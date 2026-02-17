#!/usr/bin/env python3
# step 1 png_to_mask.py (Robust "Long Line" Filter Version)
import cv2
import numpy as np
from pathlib import Path
from path_config import load_chart_runtime

# ==========================
# PATH CONFIG
# ==========================

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR, DEBUG_DIR, runtime_config = load_chart_runtime(BASE_DIR)

CHART_CONFIG = runtime_config["charts"]
FILENAME_TO_CONFIG = {cfg["filename"]: cfg for cfg in CHART_CONFIG.values()}

# ==========================
# CORE ALGORITHM
# ==========================

def extract_curve_robust(plot_img):
    h, w = plot_img.shape[:2]
    gray = cv2.cvtColor(plot_img, cv2.COLOR_BGR2GRAY)
    
    # 1. 自適應二值化
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY_INV, 25, 10
    )

    # 2. 動態長線過濾 (移除網格)
    min_line_w, min_line_h = w // 3, h // 3
    grid_h = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (min_line_w, 1)))
    grid_v = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_line_h)))
    grid_mask = cv2.bitwise_or(grid_h, grid_v)

    # 3. 減法運算得到初步清理的圖像
    clean_raw = cv2.bitwise_and(binary, cv2.bitwise_not(grid_mask))

    # 4. [優化重點] 徹底清理毛刺 (De-burr)
    # 使用 3x3 橢圓核：既能填補 1px 的交叉斷點，又能剔除細小突起
    deburr_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    
    # A. 先「閉」：填補被網格切開的小缺口，確保曲線連貫
    temp = cv2.morphologyEx(clean_raw, cv2.MORPH_CLOSE, deburr_kernel)
    
    # B. 再「開」：移除像「汗毛」一樣突出的孤立像素或細小毛刺
    # 如果毛刺比較頑固，可以考慮將核改為 (5, 5)，但 (3, 3) 最安全
    clean_healed = cv2.morphologyEx(temp, cv2.MORPH_OPEN, deburr_kernel)

    # 5. 提取最大連通域
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(clean_healed, connectivity=8)
    
    mask = np.zeros_like(gray)
    if num_labels > 1:
        largest_label = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
        mask[labels == largest_label] = 255
        
    return clean_raw, mask

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
    # raw 用於查看去網格後的「斷裂」狀態，mask 是修復後的最終結果
    raw, mask = extract_curve_robust(plot)

    # 3. Save Debug Outputs
    cv2.imwrite(str(DEBUG_DIR / f"{stem}_plot_crop.png"), plot)
    cv2.imwrite(str(DEBUG_DIR / f"{stem}_grid_removed.png"), raw)
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
