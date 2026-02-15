#!/usr/bin/env python3
import cv2
import numpy as np
import sys
from pathlib import Path

# ==========================
# CONFIG (FIL SAMPLE)
# ==========================
PLOT_BBOX = [73, 50, 591, 417]  # [x0,y0,x1,y1]

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

def main():
    if len(sys.argv) != 2:
        print("Usage: python png_to_mask.py <image_path>")
        sys.exit(1)

    img_path = Path(sys.argv[1]).resolve()

    if not img_path.exists():
        raise FileNotFoundError(f"Image not found: {img_path}")

    img = cv2.imread(str(img_path))
    if img is None:
        raise RuntimeError("OpenCV failed to load image")

    plot = crop_plot(img, PLOT_BBOX)
    gray = cv2.cvtColor(plot, cv2.COLOR_BGR2GRAY)

    out_dir = img_path.parent / "debug_mask"
    out_dir.mkdir(exist_ok=True)

    cv2.imwrite(str(out_dir / "plot_crop.png"), plot)

    cleaned = remove_grid_and_axes(gray)
    cv2.imwrite(str(out_dir / "grid_removed.png"), cleaned)

    mask = extract_curve_mask(cleaned)
    cv2.imwrite(str(out_dir / "mask_curve.png"), mask)

    print("Done.")
    print("Output directory:", out_dir)

if __name__ == "__main__":
    main()
