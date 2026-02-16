#!/usr/bin/env python3
# png_to_chart_config.py (First Principle: Projection-Based Detection)
import cv2
import numpy as np
import json
from pathlib import Path
from path_config import load_chart_runtime

# ==========================
# PATH CONFIG
# ==========================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "chart_config.json"
RAW_DIR, DEBUG_DIR, runtime_config = load_chart_runtime(BASE_DIR, CONFIG_PATH)

def detect_plot_bbox(img_path: Path):
    """
    第一性原理：利用水平/垂直投影密度鎖定最外層的坐標軸。
    """
    img = cv2.imread(str(img_path))
    if img is None: return None
    
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 1. 二值化：讓線條成為白色 (255)
    # 使用大窗口的自適應二值化，確保粗細軸線都能被捕捉
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY_INV, 31, 15
    )

    # 2. 形態學清理：只保留長度超過 15% 的線段，徹底移除文字
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 7, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 7))
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
    
    # 3. 投影分析 (Projection Profiling)
    # 計算每一行/每一列的像素總和
    row_sums = np.sum(h_lines, axis=1) # 水平線的垂直投影
    col_sums = np.sum(v_lines, axis=0) # 垂直線的水平投影

    # 4. 尋找最外層的波峰 (閾值設為最大值的 30%)
    # 這能確保我們抓到的是軸線，而不是隨機噪點
    rows_with_lines = np.where(row_sums > (np.max(row_sums) * 0.3))[0]
    cols_with_lines = np.where(col_sums > (np.max(col_sums) * 0.3))[0]

    if rows_with_lines.size < 2 or cols_with_lines.size < 2:
        return None

    # 取得最外層邊界
    y0, y1 = int(rows_with_lines[0]), int(rows_with_lines[-1])
    x0, x1 = int(cols_with_lines[0]), int(cols_with_lines[-1])

    # 稍微向外擴展 1-2 像素以確保不切到線條邊緣
    return [max(0, x0-1), max(0, y0-1), min(w, x1+1), min(h, y1+1)]

def main():
    if not CONFIG_PATH.exists(): return

    with open(CONFIG_PATH, 'r') as f:
        config_data = json.load(f)

    charts = config_data.get("charts", {})
    updated_count = 0

    for chart_id, cfg in charts.items():
        filename = cfg.get("filename")
        img_path = RAW_DIR / filename
        if not img_path.exists(): continue

        bbox = detect_plot_bbox(img_path)
        if bbox:
            cfg["plot_bbox"] = bbox
            updated_count += 1
            print(f"[OK] {filename} -> BBox: {bbox}")
            
            # 保存 Debug 圖
            img = cv2.imread(str(img_path))
            cv2.rectangle(img, (bbox[0], bbox[1]), (bbox[2], bbox[3]), (0, 255, 0), 2)
            cv2.imwrite(str(DEBUG_DIR / f"auto_bbox_{filename}"), img)

    if updated_count > 0:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config_data, f, indent=2)
        print(f"\nSuccessfully updated {updated_count} charts.")

if __name__ == "__main__":
    main()