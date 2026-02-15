#!/usr/bin/env python3
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import cv2
import numpy as np

# ==========================
# PATH CONFIG (cross-platform)
# ==========================
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = (BASE_DIR / "../../data/chart/raw").resolve()

# ==========================
# CHART CONFIG (ONLY FIL + FIV for now)
# ==========================
CHART_CONFIG: Dict[str, Dict[str, Any]] = {
    "FIL": {
        "filename": "9f4c7cf6-d991-4242-a6b4-debd4ff71ed3.png",
        "plot_bbox": [73, 50, 591, 417],  # 已测
        "x_min": 0.0, "x_max": 300.0,     # IF (mA)
        "y_min": 0.0, "y_max": 3.5,       # Relative intensity
        "swap_xy": False,                 # y = f(x)
    },
    "FIV": {
        "filename": "Weixin Image_20260214170155_250_28.png",
        "plot_bbox": [77, 45, 587, 405],        # TODO: 你量完填这里
        "x_min": 2.5, "x_max": 3.1,       # VF (V)
        "y_min": 0.0, "y_max": 300.0,     # IF (mA)
        "swap_xy": True,                  # 最终要 VF = f(IF)
    },
}

# Fit policy
DEGREE_DEFAULT = 6
DEGREE_MIN = 4

# Quality gates (工程上够用，后续可调)
GATE_MIN_R2 = 0.995          # FIL/FIV 通常很高
GATE_MAX_REL_ERR = 0.03      # 3% 以内
GATE_MAX_END_ERR = 0.05      # 端点误差（单位 y），先宽松

# ==========================
# Utilities
# ==========================

def crop_plot(img: np.ndarray, bbox: List[int]) -> np.ndarray:
    x0, y0, x1, y1 = bbox
    return img[y0:y1, x0:x1]

def ensure_bbox(cfg: Dict[str, Any]) -> None:
    bbox = cfg["plot_bbox"]
    if bbox == [0, 0, 0, 0]:
        raise RuntimeError("plot_bbox not set")

def axis_map_px_to_unit(
    xp: np.ndarray, yp: np.ndarray,
    plot_w: int, plot_h: int,
    x_min: float, x_max: float,
    y_min: float, y_max: float,
) -> Tuple[np.ndarray, np.ndarray]:
    # xp, yp are in plot-local coords (0..W-1, 0..H-1)
    x_norm = xp / max(plot_w - 1, 1)
    y_norm = (plot_h - 1 - yp) / max(plot_h - 1, 1)
    x_u = x_min + x_norm * (x_max - x_min)
    y_u = y_min + y_norm * (y_max - y_min)
    return x_u, y_u

def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

# ==========================
# Step-1: Mask (grid/axes removal + best component)
# ==========================

def _binary_foreground(gray: np.ndarray) -> np.ndarray:
    g = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.adaptiveThreshold(
        g, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        41, 3
    )

def _detect_long_lines(bin_fg: np.ndarray) -> np.ndarray:
    h, w = bin_fg.shape[:2]
    hk = max(int(w * 0.20), 25)
    vk = max(int(h * 0.20), 25)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (hk, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, vk))
    h_lines = cv2.morphologyEx(bin_fg, cv2.MORPH_OPEN, h_kernel)
    v_lines = cv2.morphologyEx(bin_fg, cv2.MORPH_OPEN, v_kernel)
    line_mask = cv2.bitwise_or(h_lines, v_lines)
    line_mask = cv2.dilate(line_mask, np.ones((3, 3), np.uint8), iterations=1)
    return line_mask

def remove_grid_and_axes(gray: np.ndarray, debug_dir: Optional[Path] = None) -> np.ndarray:
    bin_fg = _binary_foreground(gray)
    line_mask = _detect_long_lines(bin_fg)
    curve_fg = cv2.bitwise_and(bin_fg, cv2.bitwise_not(line_mask))
    curve_fg = cv2.morphologyEx(curve_fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    if debug_dir:
        cv2.imwrite(str(debug_dir / "bin_fg.png"), bin_fg)
        cv2.imwrite(str(debug_dir / "line_mask.png"), line_mask)
        cv2.imwrite(str(debug_dir / "grid_removed.png"), curve_fg)
    return curve_fg

def _component_score(bin_img: np.ndarray, labels: np.ndarray, cid: int) -> float:
    ys, xs = np.where(labels == cid)
    if xs.size < 50:
        return -1e9
    H, W = bin_img.shape[:2]
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    w = (x1 - x0 + 1)
    h = (y1 - y0 + 1)
    area = float(xs.size)
    x_cov = w / max(W, 1)

    pts = np.stack([xs.astype(np.float32), ys.astype(np.float32)], axis=1)
    pts -= pts.mean(axis=0, keepdims=True)
    cov = (pts.T @ pts) / max(pts.shape[0], 1)
    evals, _ = np.linalg.eigh(cov)
    evals = np.maximum(evals, 1e-9)
    linearity = float(evals.max() / evals.min())
    line_penalty = math.log10(linearity)

    score = (2.5 * x_cov) + (0.35 * math.log(max(area, 1.0))) - (0.8 * line_penalty)

    # penalize obvious grid/border
    if (w > 0.85 * W and h < 0.05 * H) or (h > 0.85 * H and w < 0.05 * W):
        score -= 2.0
    return score

def extract_curve_mask(curve_fg: np.ndarray, debug_dir: Optional[Path] = None) -> np.ndarray:
    bin_img = (curve_fg > 0).astype(np.uint8) * 255
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(bin_img, connectivity=8)
    if num_labels <= 1:
        return bin_img

    areas = stats[1:, cv2.CC_STAT_AREA].astype(np.float32)
    order = np.argsort(-areas)
    topN = order[: min(12, order.size)] + 1

    best_id, best_score = None, -1e18
    for cid in topN:
        s = _component_score(bin_img, labels, int(cid))
        if s > best_score:
            best_score, best_id = s, int(cid)

    out = np.zeros_like(bin_img)
    if best_id is not None:
        out[labels == best_id] = 255

    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    if debug_dir:
        cv2.imwrite(str(debug_dir / "mask_curve.png"), out)
    return out

# ==========================
# Step-2: Trace curve points (px)
# ==========================

def trace_curve_points_px(mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Efficient tracer for single-curve mask:
    For each x column, take median y of foreground pixels.
    Then fill gaps by interpolation on missing columns.
    Returns (xp, yp) in plot-local coordinates, sorted by xp.
    """
    H, W = mask.shape[:2]
    fg = (mask > 0)

    ys_med = np.full(W, np.nan, dtype=np.float32)
    count_per_x = np.zeros(W, dtype=np.int32)

    for x in range(W):
        ys = np.where(fg[:, x])[0]
        count_per_x[x] = int(ys.size)
        if ys.size:
            ys_med[x] = float(np.median(ys))

    valid = np.isfinite(ys_med)
    valid_idx = np.where(valid)[0]

    info = {
        "width": int(W),
        "height": int(H),
        "x_coverage": float(valid.mean()),
        "max_count_per_x": int(count_per_x.max()) if W else 0,
    }

    if valid_idx.size < max(10, W * 0.1):
        info["status"] = "fail_too_few_points"
        return np.array([], dtype=np.float32), np.array([], dtype=np.float32), info

    # Fill gaps by linear interpolation in x
    xs = np.arange(W, dtype=np.float32)
    ys_fill = ys_med.copy()
    ys_fill[~valid] = np.interp(xs[~valid], xs[valid], ys_med[valid])

    # Build point set (optionally downsample later)
    xp = xs
    yp = ys_fill

    # basic gap metric
    gaps = np.diff(valid_idx)
    info["largest_gap_px"] = int(gaps.max()) if gaps.size else 0
    info["status"] = "ok"
    return xp.astype(np.float32), yp.astype(np.float32), info

def draw_trace_overlay(plot_bgr: np.ndarray, xp: np.ndarray, yp: np.ndarray) -> np.ndarray:
    out = plot_bgr.copy()
    if xp.size == 0:
        return out
    pts = np.stack([xp, yp], axis=1).round().astype(np.int32)
    pts[:, 0] = np.clip(pts[:, 0], 0, out.shape[1] - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, out.shape[0] - 1)
    for i in range(1, pts.shape[0]):
        cv2.line(out, tuple(pts[i-1]), tuple(pts[i]), (0, 0, 255), 1)
    return out

# ==========================
# Step-3: Robust polynomial fit (RANSAC preferred)
# ==========================

def fit_poly_robust(x: np.ndarray, y: np.ndarray, degree: int) -> Tuple[np.ndarray, Dict[str, float], str]:
    """
    Returns (coeffs_c0_to_cd, metrics, method)
    """
    assert x.ndim == 1 and y.ndim == 1 and x.size == y.size
    n = x.size
    if n < max(30, degree * 10):
        raise RuntimeError("Too few points for fit")

    # Downsample for speed (curve is smooth). Keep endpoints.
    max_n = 600
    if n > max_n:
        idx = np.linspace(0, n - 1, max_n).round().astype(int)
        x = x[idx]
        y = y[idx]

    # Try sklearn RANSAC if available
    try:
        from sklearn.linear_model import LinearRegression, RANSACRegressor
        # build Vandermonde with bias column first: [1, x, x^2, ...]
        X = np.vstack([x**k for k in range(degree + 1)]).T
        base = LinearRegression(fit_intercept=False)
        ransac = RANSACRegressor(
            estimator=base,
            min_samples=max(degree + 1, int(0.5 * X.shape[0])),
            residual_threshold=np.std(y) * 0.5 + 1e-6,
            random_state=0,
        )
        ransac.fit(X, y)
        coef = ransac.estimator_.coef_.astype(np.float64)

        y_hat = X @ coef
        method = "ransac"
    except Exception:
        # Fallback: iterative Huber-like reweighting
        X = np.vstack([x**k for k in range(degree + 1)]).T.astype(np.float64)
        coef = np.linalg.lstsq(X, y, rcond=None)[0]
        for _ in range(10):
            r = (X @ coef) - y
            s = np.median(np.abs(r)) + 1e-9
            # Huber weights
            k = 1.345 * s
            w = 1.0 / np.maximum(1.0, np.abs(r) / k)
            W = w[:, None]
            coef = np.linalg.lstsq(X * W, y * w, rcond=None)[0]
        y_hat = X @ coef
        method = "huber_irls"

    # metrics
    resid = y_hat - y
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean())**2) + 1e-12)
    r2 = 1.0 - ss_res / ss_tot
    rmse = float(np.sqrt(np.mean(resid**2)))

    # relative error (guard near-zero y)
    denom = np.maximum(np.abs(y), 1e-6)
    rel_err = np.abs(resid) / denom
    max_rel = float(np.max(rel_err))

    # endpoint error (first/last 5% in x)
    m = max(5, int(0.05 * y.size))
    end_err = float(max(np.max(np.abs(resid[:m])), np.max(np.abs(resid[-m:]))))

    metrics = {"r2": float(r2), "rmse": rmse, "max_rel_err": max_rel, "end_err": end_err}
    return coef.astype(np.float64), metrics, method

def choose_degree_and_fit(x: np.ndarray, y: np.ndarray) -> Tuple[int, np.ndarray, Dict[str, float], str]:
    best = None
    for deg in range(DEGREE_DEFAULT, DEGREE_MIN - 1, -1):
        try:
            coef, metrics, method = fit_poly_robust(x, y, deg)
        except Exception:
            continue

        ok = (metrics["r2"] >= GATE_MIN_R2) and (metrics["max_rel_err"] <= GATE_MAX_REL_ERR) and (metrics["end_err"] <= GATE_MAX_END_ERR)
        if ok:
            return deg, coef, metrics, method

        # keep best by rmse if none passes
        if best is None or metrics["rmse"] < best[2]["rmse"]:
            best = (deg, coef, metrics, method)

    if best is None:
        raise RuntimeError("Fit failed for all degrees")
    return best

def eval_poly(coef: np.ndarray, x: np.ndarray) -> np.ndarray:
    # coef is c0..cd
    y = np.zeros_like(x, dtype=np.float64)
    p = np.ones_like(x, dtype=np.float64)
    for c in coef:
        y += c * p
        p *= x
    return y

def draw_fit_overlay(plot_bgr: np.ndarray, xp: np.ndarray, yp: np.ndarray, y_fit_px: np.ndarray) -> np.ndarray:
    out = plot_bgr.copy()
    if xp.size == 0:
        return out
    pts_data = np.stack([xp, yp], axis=1).round().astype(np.int32)
    pts_fit = np.stack([xp, y_fit_px], axis=1).round().astype(np.int32)
    for pts, color in [(pts_data, (0, 0, 255)), (pts_fit, (0, 255, 0))]:
        pts[:, 0] = np.clip(pts[:, 0], 0, out.shape[1] - 1)
        pts[:, 1] = np.clip(pts[:, 1], 0, out.shape[0] - 1)
        for i in range(1, pts.shape[0]):
            cv2.line(out, tuple(pts[i-1]), tuple(pts[i]), color, 1)
    return out

# ==========================
# End-to-end per chart
# ==========================

def process_chart(chart_id: str, cfg: Dict[str, Any], out_root: Path) -> Dict[str, Any]:
    ensure_bbox(cfg)

    img_path = out_root / cfg["filename"]
    if not img_path.exists():
        raise FileNotFoundError(f"Missing PNG: {img_path}")

    img = cv2.imread(str(img_path))
    if img is None:
        raise RuntimeError(f"OpenCV failed to load: {img_path}")

    plot = crop_plot(img, cfg["plot_bbox"])
    gray = cv2.cvtColor(plot, cv2.COLOR_BGR2GRAY)

    debug_dir = out_root / f"{img_path.stem}_debug"
    debug_dir.mkdir(exist_ok=True)

    cv2.imwrite(str(debug_dir / "plot_crop.png"), plot)

    curve_fg = remove_grid_and_axes(gray, debug_dir=debug_dir)
    mask = extract_curve_mask(curve_fg, debug_dir=debug_dir)

    # Step-2 trace
    xp, yp, trace_info = trace_curve_points_px(mask)
    trace_overlay = draw_trace_overlay(plot, xp, yp)
    cv2.imwrite(str(debug_dir / "trace_overlay.png"), trace_overlay)

    if xp.size == 0:
        return {
            "chart_id": chart_id,
            "status": "trace_failed",
            "trace_info": trace_info,
        }

    # Step-2b map to unit
    H, W = mask.shape[:2]
    x_u, y_u = axis_map_px_to_unit(
        xp, yp, plot_w=W, plot_h=H,
        x_min=cfg["x_min"], x_max=cfg["x_max"],
        y_min=cfg["y_min"], y_max=cfg["y_max"],
    )

    # swap for FIV to make VF = f(IF)
    if cfg.get("swap_xy", False):
        # original: x_u = VF, y_u = IF -> want x=IF, y=VF
        x_fit = y_u.astype(np.float64)
        y_fit = x_u.astype(np.float64)
    else:
        x_fit = x_u.astype(np.float64)
        y_fit = y_u.astype(np.float64)

    # Fit 6->5->4
    deg_used, coef, metrics, method = choose_degree_and_fit(x_fit, y_fit)

    # Make coeffs length 7 (0..6)
    coef7 = np.zeros(7, dtype=np.float64)
    coef7[: (deg_used + 1)] = coef[: (deg_used + 1)]

    # For debug: overlay fit in pixel space (optional)
    # Compute fitted y in UNIT then map back to pixel for overlay:
    y_fit_unit = eval_poly(coef, x_fit)
    if cfg.get("swap_xy", False):
        # y_fit_unit is VF, x_fit is IF; for pixel mapping we need (VF,IF) in original axis roles
        VF_u = y_fit_unit
        IF_u = x_fit
        # map (VF, IF) -> (xp_fit, yp_fit)
        xp_fit = (VF_u - cfg["x_min"]) / (cfg["x_max"] - cfg["x_min"]) * (W - 1)
        yp_fit = (H - 1) - (IF_u - cfg["y_min"]) / (cfg["y_max"] - cfg["y_min"]) * (H - 1)
    else:
        xp_fit = (x_fit - cfg["x_min"]) / (cfg["x_max"] - cfg["x_min"]) * (W - 1)
        yp_fit = (H - 1) - (y_fit_unit - cfg["y_min"]) / (cfg["y_max"] - cfg["y_min"]) * (H - 1)

    fit_overlay = draw_fit_overlay(plot, xp, yp, yp_fit.astype(np.float32))
    cv2.imwrite(str(debug_dir / "fit_overlay.png"), fit_overlay)

    # Save points for sanity
    pts_out = {
        "chart_id": chart_id,
        "swap_xy": bool(cfg.get("swap_xy", False)),
        "trace_info": trace_info,
        "x_unit": x_fit.tolist(),
        "y_unit": y_fit.tolist(),
    }
    save_json(debug_dir / "curve_points_unit.json", pts_out)

    return {
        "chart_id": chart_id,
        "status": "ok",
        "degree_used": int(deg_used),
        "method": method,
        "metrics": metrics,
        "coeff_0_to_6": coef7.tolist(),
        "debug_dir": str(debug_dir),
    }

# ==========================
# Bundle output for algorithm.py
# ==========================

def make_empty_coeffs(prefix: str) -> Dict[str, float]:
    return {f"{prefix}_{i}": 0.0 for i in range(7)}

def main():
    if not RAW_DIR.exists():
        raise RuntimeError(f"RAW_DIR not found: {RAW_DIR}")

    # Only process FIL + FIV
    targets = ["FIL", "FIV"]

    results = {}
    bundle: Dict[str, Any] = {
        "pipeline": "png->mask->trace->fit",
        "targets": targets,
        "charts": {},
        "coeffs": {},
    }

    # init all coeffs (also reserve other charts as zeros for future)
    bundle["coeffs"].update(make_empty_coeffs("FIL"))
    bundle["coeffs"].update(make_empty_coeffs("FIV"))
    bundle["coeffs"].update(make_empty_coeffs("FTL"))
    bundle["coeffs"].update(make_empty_coeffs("FTV"))

    for chart_id in targets:
        cfg = CHART_CONFIG[chart_id]
        try:
            r = process_chart(chart_id, cfg, RAW_DIR)
        except Exception as e:
            r = {"chart_id": chart_id, "status": "error", "error": str(e)}
        bundle["charts"][chart_id] = r

        if r.get("status") == "ok":
            coef7 = r["coeff_0_to_6"]
            for i in range(7):
                bundle["coeffs"][f"{chart_id}_{i}"] = float(coef7[i])

    out_path = RAW_DIR / "CoeffBundle.json"
    save_json(out_path, bundle)
    print("Done.")
    print("Wrote:", out_path)

if __name__ == "__main__":
    main()
