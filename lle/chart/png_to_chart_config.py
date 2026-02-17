#!/usr/bin/env python3
# png_to_chart_config.py (First Principle: Projection-Based Detection)
import cv2
import numpy as np
import json
from pathlib import Path

# ==========================
# PATH CONFIG
# ==========================
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = (BASE_DIR / "../../data/chart/raw").resolve()
CONFIG_PATH = RAW_DIR / "chart_config.json"
DEBUG_DIR = RAW_DIR / "debug"

FIT_DEFAULT = {"max_degree": 6, "min_degree": 4}
CHART_DEFAULTS = {
    "FIL": {"domain": {"x_min": 0.0, "x_max": 160.0, "y_min": 0.0, "y_max": 1.2}, "swap_xy": False},
    "FIV": {"domain": {"x_min": 2.55, "x_max": 3.35, "y_min": 0.0, "y_max": 160.0}, "swap_xy": True},
    "FTL": {"domain": {"x_min": 25.0, "x_max": 85.0, "y_min": 0.85, "y_max": 1.0}, "swap_xy": False},
    "FTV": {"domain": {"x_min": 25.0, "x_max": 85.0, "y_min": 0.9, "y_max": 1.0}, "swap_xy": False},
}
SUFFIX_ALIAS = {"FVI": "FIV"}

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


def infer_chart_id(filename: str) -> str:
    suffix = Path(filename).stem.split("_")[-1].upper()
    return SUFFIX_ALIAS.get(suffix, suffix)


def main():
    if not RAW_DIR.exists():
        print(f"[WARN] RAW_DIR not found: {RAW_DIR}")
        return

    png_files = sorted(p for p in RAW_DIR.glob("*.png") if p.is_file())
    charts = {}
    detected_count = 0

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    for img_path in png_files:
        filename = img_path.name
        if filename.startswith("auto_bbox_"):
            continue

        base_chart_id = infer_chart_id(filename)
        chart_id = base_chart_id
        if chart_id in charts:
            chart_id = img_path.stem

        bbox = detect_plot_bbox(img_path)
        if bbox:
            detected_count += 1
            print(f"[OK] {filename} -> BBox: {bbox}")
            img = cv2.imread(str(img_path))
            cv2.rectangle(img, (bbox[0], bbox[1]), (bbox[2], bbox[3]), (0, 255, 0), 2)
            cv2.imwrite(str(DEBUG_DIR / f"{filename}_bbox"), img)
        else:
            bbox = [0, 0, 0, 0]
            print(f"[WARN] {filename} -> BBox not found")

        cfg = {
            "filename": filename,
            "plot_bbox": bbox,
            **CHART_DEFAULTS.get(
                base_chart_id,
                {"domain": {"x_min": 0.0, "x_max": 1.0, "y_min": 0.0, "y_max": 1.0}, "swap_xy": False},
            ),
        }
        charts[chart_id] = cfg

    config_data = {
        "fit": FIT_DEFAULT,
        "charts": charts,
    }

    with open(CONFIG_PATH, "w") as f:
        json.dump(config_data, f, indent=2)

    print(f"\nCreated {CONFIG_PATH.name} from scratch.")
    print(f"Charts: {len(charts)}, detected bbox: {detected_count}")

if __name__ == "__main__":
    main()
