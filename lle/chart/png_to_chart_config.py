#!/usr/bin/env python3
# png_to_chart_config.py (Auto-detect Plot Boundary)
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
    第一性原理：圖表邊界是由最長的水平線和垂直線構成的。
    """
    img = cv2.imread(str(img_path))
    if img is None: return None
    
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 1. 二值化 (反轉，讓線條變為白色 255)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY_INV, 21, 10
    )

    # 2. 提取長線 (使用圖片尺寸的 20% 作為閾值)
    min_len_w = w // 5
    min_len_h = h // 5
    
    # 水平長線
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (min_len_w, 1))
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    
    # 垂直長線
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len_h))
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
    
    # 合併所有長線
    grid_mask = cv2.bitwise_or(h_lines, v_lines)
    
    # 3. 尋找所有長線像素的邊界
    coords = cv2.findNonZero(grid_mask)
    if coords is None:
        return None

    x, y, bw, bh = cv2.boundingRect(coords)
    
    # 返回格式 [x0, y0, x1, y1]
    return [int(x), int(y), int(x + bw), int(y + bh)]

def main():
    if not CONFIG_PATH.exists():
        print(f"Error: {CONFIG_PATH} not found.")
        return

    # 載入現有配置
    with open(CONFIG_PATH, 'r') as f:
        config_data = json.load(f)

    charts = config_data.get("charts", {})
    updated_count = 0

    # 遍歷配置中的每一個圖表
    for chart_id, cfg in charts.items():
        filename = cfg.get("filename")
        img_path = RAW_DIR / filename
        
        if not img_path.exists():
            print(f"[SKIP] File not found: {filename}")
            continue

        print(f"[PROCESSING] {filename}...")
        bbox = detect_plot_bbox(img_path)
        
        if bbox:
            # 更新配置
            cfg["plot_bbox"] = bbox
            updated_count += 1
            print(f"  -> Detected BBox: {bbox}")
            
            # 可選：保存 Debug 圖確認自動識別是否準確
            img = cv2.imread(str(img_path))
            cv2.rectangle(img, (bbox[0], bbox[1]), (bbox[2], bbox[3]), (0, 255, 0), 2)
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(DEBUG_DIR / f"auto_bbox_{filename}"), img)
        else:
            print(f"  -> [FAILED] Could not detect grid lines.")

    # 寫回 JSON 文件
    if updated_count > 0:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config_data, f, indent=2)
        print(f"\n[DONE] Successfully updated {updated_count} charts in chart_config.json")
    else:
        print("\n[FINISH] No changes made.")

if __name__ == "__main__":
    main()