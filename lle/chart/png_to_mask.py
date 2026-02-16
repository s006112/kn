#!/usr/bin/env python3
import json
import cv2
import numpy as np
from pathlib import Path
from typing import Optional

# ==========================
# PATH CONFIG
# ==========================

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR  = (BASE_DIR / "../../data/chart/raw").resolve()
CONFIG_PATH = BASE_DIR / "chart_config.json"

with open(CONFIG_PATH, "r") as f:
    CHART_CONFIG = json.load(f)["charts"]


# Build reverse index
FILENAME_TO_CONFIG = {
    cfg["filename"]: cfg
    for cfg in CHART_CONFIG.values()
}

# ==========================
# CORE
# ==========================

def crop_plot(img, bbox):
    x0, y0, x1, y1 = bbox
    return img[y0:y1, x0:x1]

def remove_grid_and_axes(gray, canny_low=50, canny_high=150, debug_dir: Optional[Path] = None):
    edges = cv2.Canny(gray, canny_low, canny_high)

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25,1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1,25))

    h_lines = cv2.morphologyEx(edges, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(edges, cv2.MORPH_OPEN, v_kernel)

    grid_mask = cv2.bitwise_or(h_lines, v_lines)

    cleaned = cv2.inpaint(gray, grid_mask, 3, cv2.INPAINT_TELEA)
    return cleaned

def extract_curve_mask(gray_clean):
    bin_img = cv2.adaptiveThreshold(
        gray_clean,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        41,
        2
    )

    kernel = np.ones((3,3), np.uint8)
    opened = cv2.morphologyEx(bin_img, cv2.MORPH_OPEN, kernel)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)

    if num_labels <= 1:
        return opened

    largest = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    mask = np.zeros_like(opened)
    mask[labels == largest] = 255

    return mask

# ==========================
# PIPELINE
# ==========================

def process_image(img_path: Path):
    cfg = FILENAME_TO_CONFIG.get(img_path.name)

    if cfg is None:
        print(f"[SKIP] No config for: {img_path.name}")
        return

    bbox = cfg["plot_bbox"]

    if bbox == [0,0,0,0]:
        print(f"[SKIP] BBOX not set for: {img_path.name}")
        return

    img = cv2.imread(str(img_path))
    if img is None:
        print(f"[SKIP] Cannot load: {img_path.name}")
        return

    plot = crop_plot(img, bbox)
    grey = cv2.cvtColor(plot, cv2.COLOR_BGR2GRAY)

    out_dir = img_path.parent / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = img_path.stem

    cv2.imwrite(str(out_dir / f"{stem}_dplot_crop.png"), plot)
    cv2.imwrite(str(out_dir / f"{stem}_plot_crop.png"), plot)

    cleaned = remove_grid_and_axes(grey)
    cv2.imwrite(str(out_dir / f"{stem}_grid_removed.png"), cleaned)

    mask = extract_curve_mask(cleaned)
    cv2.imwrite(str(out_dir / f"{stem}_mask_curve.png"), mask)

    print(f"[OK] {img_path.name}")

def main():
    if not RAW_DIR.exists():
        raise RuntimeError(f"RAW_DIR not found: {RAW_DIR}")

    png_files = sorted(RAW_DIR.glob("*.png"))

    if not png_files:
        print("No PNG files found.")
        return

    print(f"Found {len(png_files)} PNG files.")

    for img_path in png_files:
        process_image(img_path)

    print("All done.")

if __name__ == "__main__":
    main()
