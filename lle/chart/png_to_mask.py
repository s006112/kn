#!/usr/bin/env python3
"""
Own file name: png_to_mask.py

Responsibility:
Generate debug artifacts for chart PNG inputs by cropping a fixed plot area, removing grid and axis lines, and extracting a binary curve mask for each image in the raw chart directory.

Pipelines:
- input_png -> crop -> grayscale -> edge_detect -> line_mask -> inpaint -> threshold -> open -> components -> curve_mask -> debug_outputs

Invariants:
- Input discovery is limited to `*.png` files under `RAW_DIR`.
- Plot cropping always uses the fixed `PLOT_BBOX` coordinates.
- Output artifacts are written to a sibling debug directory named `{stem}_debug_mask`.
- Execution continues per-file unless image loading fails for that file.

Out of scope:
- Automatic plot bounding-box detection.
- Multi-curve segmentation.
- Coordinate extraction from mask pixels.
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Optional

# Configuration constants for filesystem and crop bounds.

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR  = BASE_DIR / "../../data/chart/raw"
RAW_DIR  = RAW_DIR.resolve()

# Fixed bounding box used to crop the chart plot area.
PLOT_BBOX = [73, 50, 591, 417]

def _write_canny_intermediates(gray, edges, low_thresh, high_thresh, out_dir: Path, stem: str):
    """
    Purpose:
    Write intermediate PNGs to visualize what Canny is reacting to.
    Notes:
    - OpenCV's Canny applies non-maximum suppression + hysteresis internally.
    - The "weak/strong" images here are a proxy based on Sobel gradient magnitude
      compared against (low_thresh, high_thresh); they won't match Canny 1:1 but
      are useful to see which pixels are likely to trigger thresholds.
    """
    out_dir.mkdir(exist_ok=True)

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = cv2.magnitude(gx, gy)

    grad_mag_vis = cv2.normalize(grad_mag, None, 0, 255, cv2.NORM_MINMAX)
    grad_mag_vis = grad_mag_vis.astype(np.uint8)

    strong = (grad_mag >= float(high_thresh)).astype(np.uint8) * 255
    weak = ((grad_mag >= float(low_thresh)) & (grad_mag < float(high_thresh))).astype(np.uint8) * 255

    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    overlay[weak > 0] = (0, 255, 255)     # yellow (weak proxy)
    overlay[strong > 0] = (0, 0, 255)     # red (strong proxy)
    overlay[edges > 0] = (255, 255, 255)  # white (final edges)

    cv2.imwrite(str(out_dir / f"{stem}_canny_edges.png"), edges)
    cv2.imwrite(str(out_dir / f"{stem}_canny_grad_mag.png"), grad_mag_vis)
    cv2.imwrite(str(out_dir / f"{stem}_canny_strong_proxy.png"), strong)
    cv2.imwrite(str(out_dir / f"{stem}_canny_weak_proxy.png"), weak)
    cv2.imwrite(str(out_dir / f"{stem}_canny_overlay.png"), overlay)

def crop_plot(img, bbox):
    """
    Purpose:
    Return the rectangular plot crop defined by pixel bounds.
    Inputs:
    - img: Source image array in HWC layout.
    - bbox: Four-integer list or tuple `[x0, y0, x1, y1]`.
    Outputs:
    - Cropped image array `img[y0:y1, x0:x1]`.
    """
    x0, y0, x1, y1 = bbox
    return img[y0:y1, x0:x1]

def remove_grid_and_axes(gray, canny_low=50, canny_high=150, debug_dir: Optional[Path] = None):
    """
    Purpose:
    Suppress grid and axis lines by detecting long horizontal and vertical edges and inpainting them.
    Inputs:
    - gray: Single-channel grayscale plot image.
    Outputs:
    - Grayscale image with detected line structures inpainted.
    """
    edges = cv2.Canny(gray, canny_low, canny_high)
    if debug_dir is not None:
        _write_canny_intermediates(
            gray=gray,
            edges=edges,
            low_thresh=canny_low,
            high_thresh=canny_high,
            out_dir=debug_dir,
            stem="grid",
        )

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25,1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1,25))

    h_lines = cv2.morphologyEx(edges, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(edges, cv2.MORPH_OPEN, v_kernel)

    grid_mask = cv2.bitwise_or(h_lines, v_lines)
    if debug_dir is not None:
        cv2.imwrite(str(debug_dir / "grid_line_mask.png"), grid_mask)

    cleaned = cv2.inpaint(gray, grid_mask, 3, cv2.INPAINT_TELEA)
    return cleaned

def extract_curve_mask(gray_clean):
    """
    Purpose:
    Build a binary mask for the dominant connected foreground component after adaptive thresholding and opening.
    Inputs:
    - gray_clean: Grayscale image after line removal.
    Outputs:
    - Binary uint8 mask where the selected curve component is 255 and background is 0.
    """
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

def process_image(img_path: Path):
    """
    Purpose:
    Run the full crop-clean-mask pipeline for one image and emit intermediate debug files.
    Inputs:
    - img_path: Path to a PNG image file.
    Outputs:
    - None. Writes `plot_crop.png`, `grid_removed.png`, and `mask_curve.png` in a per-image debug directory.
    """
    img = cv2.imread(str(img_path))
    if img is None:
        print(f"[SKIP] Cannot load: {img_path.name}")
        return

    plot = crop_plot(img, PLOT_BBOX)
    gray = cv2.cvtColor(plot, cv2.COLOR_BGR2GRAY)

    out_dir = img_path.parent / f"{img_path.stem}_debug_mask"
    out_dir.mkdir(exist_ok=True)

    cv2.imwrite(str(out_dir / "plot_crop.png"), plot)

    cleaned = remove_grid_and_axes(gray, debug_dir=out_dir)
    cv2.imwrite(str(out_dir / "grid_removed.png"), cleaned)

    mask = extract_curve_mask(cleaned)
    cv2.imwrite(str(out_dir / "mask_curve.png"), mask)

    print(f"[OK] {img_path.name}")

def main():
    """
    Purpose:
    Iterate over all PNG files in the raw chart directory and process each image.
    Inputs:
    - None.
    Outputs:
    - None. Prints progress messages and writes debug artifacts via `process_image`.
    """
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
