#!/usr/bin/env python3
import cv2
import numpy as np
from pathlib import Path

# ==========================
# CONFIG (cross-platform)
# ==========================

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR  = BASE_DIR / "../../data/chart/raw"
RAW_DIR  = RAW_DIR.resolve()

# 当前 sample bbox（FIL）
PLOT_BBOX = [73, 50, 591, 417]

# ==========================
# CORE
# ==========================

def crop_plot(img, bbox):
    x0, y0, x1, y1 = bbox
    return img[y0:y1, x0:x1]

def remove_grid_and_axes(gray):
    edges = cv2.Canny(gray, 50, 150)

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
        31,
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
    img = cv2.imread(str(img_path))
    if img is None:
        print(f"[SKIP] Cannot load: {img_path.name}")
        return

    plot = crop_plot(img, PLOT_BBOX)
    gray = cv2.cvtColor(plot, cv2.COLOR_BGR2GRAY)

    out_dir = img_path.parent / f"{img_path.stem}_debug_mask"
    out_dir.mkdir(exist_ok=True)

    cv2.imwrite(str(out_dir / "plot_crop.png"), plot)

    cleaned = remove_grid_and_axes(gray)
    cv2.imwrite(str(out_dir / "grid_removed.png"), cleaned)

    mask = extract_curve_mask(cleaned)
    cv2.imwrite(str(out_dir / "mask_curve.png"), mask)

    print(f"[OK] {img_path.name}")

def main():
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
