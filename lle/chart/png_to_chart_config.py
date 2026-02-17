#!/usr/bin/env python3
# step 0 png_to_chart_config.py (First Principle: Projection-Based Detection)
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
    "lm_test": {
        "2700": 35,
        "3000": 37,
        "3500": 39,
        "4000": 41,
        "5000": 43,
        "6500": 45,
    },
    "CRI": 80,
    "If_max": 300,
    "If": 150,
    "USD": 0.003,
    "RMB": 0.01,
    "Quote_date": "2025-12-04",
    "Vf": 0.0,
    "Tj": 0.0,
    "FIL": {"domain": {"x_min": 0.0, "x_max": 160.0, "y_min": 0.0, "y_max": 1.2}, "swap_xy": False},
    "FIV": {"domain": {"x_min": 2.55, "x_max": 3.35, "y_min": 0.0, "y_max": 160.0}, "swap_xy": True},
    "FTL": {"domain": {"x_min": 25.0, "x_max": 85.0, "y_min": 0.85, "y_max": 1.0}, "swap_xy": False},
    "FTV": {"domain": {"x_min": 25.0, "x_max": 85.0, "y_min": 0.9, "y_max": 1.0}, "swap_xy": False},
}
SUFFIX_ALIAS = {"FVI": "FIV"}
CHART_IDS = {"FIL", "FIV", "FTL", "FTV"}

def detect_plot_bbox(img_path: Path):
    img = cv2.imread(str(img_path))
    if img is None: return None
    
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 1. 強化軸線：利用形態學與更強的二值化來區分深色軸線與淺色網格
    # 使用較小的自適應窗口，並增加常數項 C 來過濾掉淺灰色網格
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY_INV, 31, 5 # 增加 C 值 (從 15 改到 20) 可過濾淺色網格
    )

    # 2. 形態學清理 (維持原樣，這部分對移除文字很有用)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 7, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 7))
    h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
    
    # 3. 投影分析
    row_sums = np.sum(h_lines, axis=1)
    col_sums = np.sum(v_lines, axis=0)

    def find_axis_center(sums, threshold_ratio=0.3):
        """
        尋找投影中最外層波峰的中心點
        """
        peaks = np.where(sums > (np.max(sums) * threshold_ratio))[0]
        if peaks.size < 2:
            return None, None
        
        # 尋找第一個和最後一個「連續塊」
        # 例如：peaks = [50, 51, 52, 500, 501] -> 軸線寬度大約 3 像素
        def get_cluster_center(indices, reverse=False):
            if reverse:
                indices = indices[::-1]
            
            cluster = [indices[0]]
            for i in range(1, len(indices)):
                # 如果像素連續(間距<3)，視為同一條軸線
                if abs(indices[i] - indices[i-1]) <= 3:
                    cluster.append(indices[i])
                else:
                    break
            return sum(cluster) / len(cluster)

        first_center = get_cluster_center(peaks, reverse=False)
        last_center = get_cluster_center(peaks, reverse=True)
        return first_center, last_center

    y0, y1 = find_axis_center(row_sums)
    x0, x1 = find_axis_center(col_sums)

    if y0 is None or x0 is None:
        return None

    # 直接回傳浮點數或四捨五入，這將會是軸線的正中心
    return [round(x0), round(y0), round(x1), round(y1)]

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
            cv2.imwrite(str(DEBUG_DIR / f"{Path(filename).stem}_bbox{Path(filename).suffix}"), img)
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

    global_defaults = {k: v for k, v in CHART_DEFAULTS.items() if k not in CHART_IDS}
    config_data = {
        "fit": FIT_DEFAULT,
        **global_defaults,
        "charts": charts,
    }

    with open(CONFIG_PATH, "w") as f:
        json.dump(config_data, f, indent=2)

    print(f"\nCreated {CONFIG_PATH.name} from scratch.")
    print(f"Charts: {len(charts)}, detected bbox: {detected_count}")

if __name__ == "__main__":
    main()
