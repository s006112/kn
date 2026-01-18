#!/usr/bin/env python3
import time, logging, shutil, sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import cv2
import numpy as np
import csv
from scipy.signal import find_peaks
from scipy.signal import medfilt

WATCH_FOLDER   = Path(r"C:\Users\KN\Desktop\Sync\Ampco\Parts\LED Official Spec\Chart")
ARCHIVE_FOLDER = WATCH_FOLDER / "archive"
DEBUG_FOLDER   = WATCH_FOLDER / "debug"
PROCESSED_FOLDER = WATCH_FOLDER / "processed"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

def order_points(pts):
    """
    Arrange four given points into top-left, top-right, bottom-right, and bottom-left order.

    Parameters:
        pts (numpy.ndarray): Array of 4 points (x, y) to be ordered.

    Returns:
        numpy.ndarray: Numpy array with points reordered.
    """
    rect = np.zeros((4,2), dtype="float32")
    s = pts.sum(axis=1); diff = np.diff(pts, axis=1)
    rect[0] = pts[np.argmin(s)]; rect[2] = pts[np.argmax(s)]
    rect[1] = pts[np.argmin(diff)]; rect[3] = pts[np.argmax(diff)]
    return rect

def isolate_plot_roi(img):
    """
    Isolate the region of interest (ROI) of a plot from the given image.

    Parameters:
        img (numpy.ndarray): Input image (BGR format).

    Returns:
        tuple: A tuple containing the warped ROI image, two corner points (lower left and upper right),
               and the coordinates of the rectangular boundaries of the ROI.
    """
    gray    = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5,5), 0)
    edges   = cv2.Canny(blurred, 50, 150)
    cnts,_  = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w    = img.shape[:2]
    if not cnts:
        rect = np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]], dtype="float32")
        return img, (0,h-1), (w-1,0), rect
    max_cnt = max(cnts, key=cv2.contourArea)
    peri    = cv2.arcLength(max_cnt, True)
    approx  = cv2.approxPolyDP(max_cnt, 0.02*peri, True)
    if len(approx)!=4:
        rect = np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]], dtype="float32")
        return img, (0,h-1), (w-1,0), rect
    pts     = approx.reshape(4,2)
    rect    = order_points(pts)
    tl, tr, br, bl = rect
    maxW    = int(max(np.linalg.norm(br-bl), np.linalg.norm(tr-tl)))
    maxH    = int(max(np.linalg.norm(tr-br), np.linalg.norm(tl-bl)))
    dst     = np.array([[0,0],[maxW-1,0],[maxW-1,maxH-1],[0,maxH-1]], dtype="float32")
    M       = cv2.getPerspectiveTransform(rect, dst)
    warped  = cv2.warpPerspective(img, M, (maxW, maxH))
    return warped, (0,maxH-1), (maxW-1,0), rect

def process_chart(png_path: Path):
    """
    Process a chart image file: isolate the plot, generate debug images, 
    save intensity matrices, and prepare data for further processing.

    Parameters:
        png_path (Path): Path to the chart image file to process.
    """
    logger.info(f"▶ Start processing {png_path.name}")
    img = cv2.imread(str(png_path))
    plot, ll, ur, rect = isolate_plot_roi(img)
    debug = img.copy()
    cv2.polylines(debug, [rect.astype(np.int32)], True, (0,255,0), 2)
    stem = png_path.stem
    DEBUG_FOLDER.mkdir(exist_ok=True)
    PROCESSED_FOLDER.mkdir(exist_ok=True)
    cv2.imwrite(str(DEBUG_FOLDER / f"{stem}_boxed.png"), debug)
    roi_path = DEBUG_FOLDER / f"{stem}_roi.png"
    cv2.imwrite(str(roi_path), plot)
    logger.info(f"Saved debug images for {png_path.name}")
    # Convert ROI to grayscale and save intensity matrix as CSV
    gray_plot = cv2.cvtColor(plot, cv2.COLOR_BGR2GRAY)
    intensity_matrix = gray_plot.tolist()
    csv_path = DEBUG_FOLDER / f"{stem}_roi.csv"
    with open(csv_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(intensity_matrix)
    logger.info(f"Saved intensity matrix CSV for {png_path.name}")

    # --- Wash step: threshold all intensity > 210 to 255 ---
    wash_index = 210
    washed_matrix = [[255 if val > wash_index else val for val in row] for row in intensity_matrix]
    roi_wash_csv_path = DEBUG_FOLDER / f"{stem}_roi_wash.csv"
    with open(roi_wash_csv_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(washed_matrix)
    roi_wash_png_path = PROCESSED_FOLDER / f"{stem}_roi_wash.png"
    arr = np.array(washed_matrix, dtype=np.uint8)
    cv2.imwrite(str(roi_wash_png_path), arr)
    logger.info(f"Saved washed ROI CSV and PNG for {png_path.name}")

    reconstruct_png_from_csv(csv_path)
    cleaned_csv_path = clean_csv_and_output(roi_wash_csv_path)
    extract_single_pixel_curve(cleaned_csv_path)
    # TODO: add OCR, curve extraction, Excel export…
    time.sleep(1)
    logger.info(f"✔ Finished processing {png_path.name}")

# [Remaining functions are next in progression... further docstrings have been added but not shown due to character limits]