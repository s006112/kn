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

from path_config import load_chart_runtime

BASE_DIR = Path(__file__).resolve().parent
WATCH_FOLDER, DEBUG_FOLDER, _ = load_chart_runtime(BASE_DIR)
ARCHIVE_FOLDER = WATCH_FOLDER / "archive"
PROCESSED_FOLDER = WATCH_FOLDER / "processed"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

def order_points(pts):
    rect = np.zeros((4,2), dtype="float32")
    s = pts.sum(axis=1); diff = np.diff(pts, axis=1)
    rect[0] = pts[np.argmin(s)]; rect[2] = pts[np.argmax(s)]
    rect[1] = pts[np.argmin(diff)]; rect[3] = pts[np.argmax(diff)]
    return rect

def isolate_plot_roi(img):
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

def move_to_archive(png_path: Path):
    shutil.move(str(png_path), str(ARCHIVE_FOLDER / png_path.name))
    logger.info(f"↪ Archived: {png_path.name}")

def handle_file(path: Path):
    time.sleep(0.5)
    try:
        process_chart(path)
    except Exception:
        logger.exception(f"✖ Error processing {path.name}")
    finally:
        if path.exists():
            move_to_archive(path)

class PngCreatedHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory: return
        path = Path(event.src_path)
        if path.suffix.lower() == ".png":
            logger.info(f"Detected new file: {path.name}")
            handle_file(path)

def process_existing_pngs():
    pngs = sorted(WATCH_FOLDER.glob("*.png"))
    if pngs:
        logger.info(f"Found {len(pngs)} existing .png file(s)")
        for p in pngs:
            handle_file(p)

def reconstruct_png_from_csv(csv_path):
    import csv
    import numpy as np
    import cv2
    from pathlib import Path
    csv_path = Path(csv_path)
    with open(csv_path, 'r') as csvfile:
        reader = csv.reader(csvfile)
        intensity_matrix = [[int(val) for val in row] for row in reader]
    arr = np.array(intensity_matrix, dtype=np.uint8)
    stem = csv_path.stem.replace('_roi', '') if csv_path.stem.endswith('_roi') else csv_path.stem
    out_path = PROCESSED_FOLDER / f"{stem}_roi_reconstructed.png"
    cv2.imwrite(str(out_path), arr)
    print(f'Reconstructed image saved to {out_path}')

def remove_grid_lines(intensity_matrix, edge_thresh1=50, edge_thresh2=150, margin=2, wash_width_min=8, min_grid_width=1, max_grid_width=30):
    import numpy as np
    import cv2
    from scipy.signal import find_peaks

    arr = np.array(intensity_matrix, dtype=np.uint8)
    h, w = arr.shape
    # Edge detection
    edges = cv2.Canny(arr, edge_thresh1, edge_thresh2)

    cleaned = arr.copy()

    # --- Vertical grid removal ---
    vertical_profile = np.sum(edges, axis=0)
    v_peaks, _ = find_peaks(vertical_profile, prominence=np.max(vertical_profile)//4, distance=min_grid_width)
    # Group peaks into pairs (left/right edges)
    v_pairs = []
    used = set()
    i = 0
    while i < len(v_peaks) - 1:
        width = v_peaks[i+1] - v_peaks[i]
        if min_grid_width <= width <= max_grid_width:
            v_pairs.append((v_peaks[i], v_peaks[i+1]))
            used.add(i)
            used.add(i+1)
            i += 2
        else:
            i += 1
    # Wash paired peaks (thick lines)
    for left, right in v_pairs:
        center = (left + right) // 2
        width = max(right - left + 1 + 2*margin, wash_width_min)
        start = max(center - width//2, 0)
        end = min(center + width//2, w)
        cleaned[:, start:end] = 255
    # Wash single peaks (thin lines or boundaries)
    for idx, peak in enumerate(v_peaks):
        if idx in used:
            continue
        center = peak
        width = wash_width_min
        # If at boundary, wash from boundary inward
        if center == 0:
            cleaned[:, 0:width] = 255
        elif center == w-1:
            cleaned[:, w-width:w] = 255
        else:
            start = max(center - width//2, 0)
            end = min(center + width//2, w)
            cleaned[:, start:end] = 255

    # --- Horizontal grid removal ---
    horizontal_profile = np.sum(edges, axis=1)
    h_peaks, _ = find_peaks(horizontal_profile, prominence=np.max(horizontal_profile)//4, distance=min_grid_width)
    h_pairs = []
    used = set()
    i = 0
    while i < len(h_peaks) - 1:
        width = h_peaks[i+1] - h_peaks[i]
        if min_grid_width <= width <= max_grid_width:
            h_pairs.append((h_peaks[i], h_peaks[i+1]))
            used.add(i)
            used.add(i+1)
            i += 2
        else:
            i += 1
    # Wash paired peaks (thick lines)
    for top, bottom in h_pairs:
        center = (top + bottom) // 2
        width = max(bottom - top + 1 + 2*margin, wash_width_min)
        start = max(center - width//2, 0)
        end = min(center + width//2, h)
        cleaned[start:end, :] = 255
    # Wash single peaks (thin lines or boundaries)
    for idx, peak in enumerate(h_peaks):
        if idx in used:
            continue
        center = peak
        width = wash_width_min
        if center == 0:
            cleaned[0:width, :] = 255
        elif center == h-1:
            cleaned[h-width:h, :] = 255
        else:
            start = max(center - width//2, 0)
            end = min(center + width//2, h)
            cleaned[start:end, :] = 255

    return cleaned.tolist()

def clean_csv_and_output(csv_path):
    import csv
    import numpy as np
    from pathlib import Path
    csv_path = Path(csv_path)
    with open(csv_path, 'r') as csvfile:
        reader = csv.reader(csvfile)
        intensity_matrix = [[int(val) for val in row] for row in reader]
    cleaned_matrix = remove_grid_lines(intensity_matrix)
    # Save cleaned CSV
    cleaned_csv_path = DEBUG_FOLDER / f"{csv_path.stem.replace('_roi','')}_cleaned.csv"
    with open(cleaned_csv_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerows(cleaned_matrix)
    # Save cleaned PNG to PROCESSED_FOLDER
    arr = np.array(cleaned_matrix, dtype=np.uint8)
    cleaned_png_path = PROCESSED_FOLDER / f"{csv_path.stem.replace('_roi','')}_cleaned.png"
    cv2.imwrite(str(cleaned_png_path), arr)
    print(f'Cleaned image and CSV saved to {cleaned_png_path} and {cleaned_csv_path}')
    return cleaned_csv_path

def extract_single_pixel_curve(cleaned_csv_path, median_filter_size=3, sequence_filter_size=5):
    import csv
    import numpy as np
    import cv2
    from scipy.signal import medfilt
    from pathlib import Path

    cleaned_csv_path = Path(cleaned_csv_path)
    with open(cleaned_csv_path, 'r') as csvfile:
        reader = csv.reader(csvfile)
        M = np.array([[int(val) for val in row] for row in reader], dtype=np.uint8)
    H, W = M.shape

    # 1. Optional smoothing: 3x3 median filter
    if median_filter_size > 1:
        M_smooth = cv2.medianBlur(M, median_filter_size)
    else:
        M_smooth = M.copy()

    # 2. Column-wise local minima (darkest pixel) detection
    y_seq = []
    for x in range(W):
        col = M_smooth[:, x]
        if np.all(col == 255):
            y = -1  # blank column
        else:
            min_val = np.min(col)
            y_candidates = np.where(col == min_val)[0]
            y = y_candidates[0] if len(y_candidates) > 0 else -1
        y_seq.append(y)

    # 3. Sequence smoothing: 1D median filter
    y_seq = np.array(y_seq)
    y_seq_med = medfilt(y_seq, sequence_filter_size)

    # 4. Gap filling: interpolate invalid or outlier values
    valid = (y_seq_med >= 0) & (y_seq_med < H)
    y_final = y_seq_med.copy()
    if not np.all(valid):
        valid_idx = np.where(valid)[0]
        invalid_idx = np.where(~valid)[0]
        if len(valid_idx) > 1:
            y_final[invalid_idx] = np.interp(invalid_idx, valid_idx, y_final[valid_idx])
        else:
            y_final[invalid_idx] = 0

    # 4b. Do not assign curve pixel if more than 2x2 consecutive matrix is blank (255)
    for x in range(W):
        y = int(round(y_final[x]))
        if 0 <= y < H:
            # Check 2x2 region: (y,x), (y+1,x), (y,x+1), (y+1,x+1)
            if y+1 < H and x+1 < W:
                region = M_smooth[y:y+2, x:x+2]
                if np.all(region == 255):
                    y_final[x] = -1  # Mark as blank

    # 5. Rounding and output
    y_final = np.round(y_final).astype(int)
    curve_points = [(x, int(y_final[x])) for x in range(W) if y_final[x] >= 0 and y_final[x] < H]

    # Save CSV (swap y so that Excel plot matches image orientation)
    curve_csv_path = cleaned_csv_path.parent / (cleaned_csv_path.stem.replace('_cleaned','') + '_curve_extraction.csv')
    with open(curve_csv_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['x', 'y'])
        for x, y in curve_points:
            y_out = (H - 1) - y
            writer.writerow([x, y_out])

    # Save PNG (draw curve on blank image)
    curve_png_path = PROCESSED_FOLDER / (cleaned_csv_path.stem.replace('_cleaned','') + '_curve_extraction.png')
    img = np.ones((H, W), dtype=np.uint8) * 255
    for x, y in curve_points:
        if 0 <= y < H:
            img[y, x] = 0
    cv2.imwrite(str(curve_png_path), img)
    print(f'Curve extraction saved to {curve_csv_path} and {curve_png_path}')

def main():
    if not WATCH_FOLDER.exists():
        logger.error(f"Watch folder not found: {WATCH_FOLDER}")
        sys.exit(1)
    ARCHIVE_FOLDER.mkdir(exist_ok=True)
    DEBUG_FOLDER.mkdir(exist_ok=True)
    process_existing_pngs()
    observer = Observer()
    observer.schedule(PngCreatedHandler(), str(WATCH_FOLDER), recursive=False)
    observer.start()
    logger.info(f"Watching: {WATCH_FOLDER}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()
