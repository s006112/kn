#!/usr/bin/env python3
# mvp1_runner.py
# MVP-1: Template-driven bbox + axis range -> curve extraction -> polyfit -> CoeffBundle + debug overlays
# Minimal deps: pymupdf(fitz), opencv-python(cv2), numpy

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Dict, Tuple, List, Any

import numpy as np
import cv2
import fitz  # PyMuPDF


CHART_IDS_DEFAULT = ["FIL"]  # MVP-1 start from FIL only


def load_template(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        tpl = json.load(f)

    # minimal validation
    assert "part_no" in tpl, "template missing part_no"
    assert "pdf_pages" in tpl and isinstance(tpl["pdf_pages"], dict), "template missing pdf_pages"
    assert "charts" in tpl and isinstance(tpl["charts"], dict), "template missing charts"

    for cid, cfg in tpl["charts"].items():
        for k in ["roi_bbox_px", "plot_bbox_px", "axis"]:
            assert k in cfg, f"template charts[{cid}] missing {k}"
        for bname in ["roi_bbox_px", "plot_bbox_px"]:
            b = cfg[bname]
            assert isinstance(b, list) and len(b) == 4, f"{cid}.{bname} must be [x0,y0,x1,y1]"
        ax = cfg["axis"]
        for k in ["x_min", "x_max", "y_min", "y_max"]:
            assert k in ax, f"{cid}.axis missing {k}"
        assert float(ax["x_min"]) < float(ax["x_max"]), f"{cid}.axis x_min must < x_max"
        assert float(ax["y_min"]) < float(ax["y_max"]), f"{cid}.axis y_min must < y_max"

        # defaults
        cfg.setdefault("swap_xy", False)
        cfg.setdefault("monotonic", "none")  # increasing/decreasing/none
        cfg.setdefault("degree_default", 6)
        cfg.setdefault("degree_min", 4)
        cfg.setdefault("n_samples", 200)
        cfg.setdefault("preprocess", {})
        cfg["preprocess"].setdefault("adaptive_block", 31)
        cfg["preprocess"].setdefault("adaptive_C", 5)
        cfg["preprocess"].setdefault("remove_grid", True)
        cfg["preprocess"].setdefault("grid_kernel_frac", 0.08)  # kernel length as frac of W/H
        cfg["preprocess"].setdefault("close_kernel", 3)

        cfg.setdefault("trim", {})
        cfg["trim"].setdefault("min_run_cols", 12)   # contiguous columns required
        cfg["trim"].setdefault("edge_guard_cols", 10)  # trim edges until this many consecutive cols have points

    return tpl


def compute_sha1(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def render_pdf_page(pdf_path: str, page_index_1based: int, dpi: int = 350) -> np.ndarray:
    doc = fitz.open(pdf_path)
    try:
        p = doc.load_page(int(page_index_1based) - 1)
        zoom = float(dpi) / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = p.get_pixmap(matrix=mat, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        # pix.n should be 3 (RGB)
        if img.shape[2] == 3:
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        else:
            img_bgr = img[:, :, :3].copy()
        return img_bgr
    finally:
        doc.close()


def _clip_bbox(bbox: List[int], w: int, h: int) -> Tuple[int, int, int, int]:
    x0, y0, x1, y1 = [int(round(v)) for v in bbox]
    x0 = max(0, min(x0, w - 1))
    x1 = max(0, min(x1, w))
    y0 = max(0, min(y0, h - 1))
    y1 = max(0, min(y1, h))
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"Invalid bbox after clip: {bbox} -> {(x0,y0,x1,y1)} for image {w}x{h}")
    return x0, y0, x1, y1


def crop_chart_regions(page_image: np.ndarray, chart_cfg: Dict[str, Any]) -> Dict[str, Any]:
    H, W = page_image.shape[:2]
    rx0, ry0, rx1, ry1 = _clip_bbox(chart_cfg["roi_bbox_px"], W, H)
    px0, py0, px1, py1 = _clip_bbox(chart_cfg["plot_bbox_px"], W, H)

    roi = page_image[ry0:ry1, rx0:rx1].copy()
    plot = page_image[py0:py1, px0:px1].copy()

    return {
        "roi": roi,
        "plot": plot,
        "roi_bbox": (rx0, ry0, rx1, ry1),
        "plot_bbox": (px0, py0, px1, py1),
        "page_shape": (H, W),
    }


def draw_bboxes_overlay(page_image: np.ndarray, roi_bbox, plot_bbox) -> np.ndarray:
    out = page_image.copy()
    (rx0, ry0, rx1, ry1) = roi_bbox
    (px0, py0, px1, py1) = plot_bbox
    cv2.rectangle(out, (rx0, ry0), (rx1, ry1), (0, 255, 255), 2)  # ROI yellow
    cv2.rectangle(out, (px0, py0), (px1, py1), (0, 255, 0), 2)    # PLOT green
    return out


def build_axis_mapping(plot_shape_hw: Tuple[int, int], axis_cfg: Dict[str, Any]) -> Dict[str, Any]:
    H, W = int(plot_shape_hw[0]), int(plot_shape_hw[1])
    x_min = float(axis_cfg["x_min"])
    x_max = float(axis_cfg["x_max"])
    y_min = float(axis_cfg["y_min"])
    y_max = float(axis_cfg["y_max"])
    x_unit = axis_cfg.get("x_unit", "")
    y_unit = axis_cfg.get("y_unit", "")

    def px_to_unit(x_px: float, y_px: float) -> Tuple[float, float]:
        # x: left->right
        xu = x_min + (float(x_px) / max(W - 1, 1)) * (x_max - x_min)
        # y: top->bottom pixel, but unit is bottom->top
        yu = y_max - (float(y_px) / max(H - 1, 1)) * (y_max - y_min)
        return xu, yu

    return {
        "H": H,
        "W": W,
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
        "x_unit": x_unit,
        "y_unit": y_unit,
        "px_to_unit": px_to_unit,
    }


def preprocess_plot_to_mask(plot_bgr: np.ndarray, cfg: Dict[str, Any]) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """
    Returns:
      mask_curve: uint8 {0,255}, where 255 indicates candidate curve pixels
      debug: dict of intermediate images
    """
    gray = cv2.cvtColor(plot_bgr, cv2.COLOR_BGR2GRAY)
    # normalize contrast a bit
    gray_eq = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)

    block = int(cfg.get("adaptive_block", 31))
    if block % 2 == 0:
        block += 1
    block = max(block, 11)
    C = int(cfg.get("adaptive_C", 5))

    bin_inv = cv2.adaptiveThreshold(
        gray_eq, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, C
    )  # curve/grid become white(255)

    dbg = {"gray": gray, "gray_eq": gray_eq, "bin_inv": bin_inv}

    mask = bin_inv.copy()

    if bool(cfg.get("remove_grid", True)):
        H, W = mask.shape[:2]
        frac = float(cfg.get("grid_kernel_frac", 0.08))
        kx = max(15, int(W * frac))
        ky = max(15, int(H * frac))

        # remove long horizontal lines
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 1))
        h_lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, h_kernel, iterations=1)

        # remove long vertical lines
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, ky))
        v_lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, v_kernel, iterations=1)

        grid = cv2.bitwise_or(h_lines, v_lines)
        mask = cv2.bitwise_and(mask, cv2.bitwise_not(grid))

        dbg["h_lines"] = h_lines
        dbg["v_lines"] = v_lines
        dbg["grid"] = grid
        dbg["no_grid"] = mask

    ck = int(cfg.get("close_kernel", 3))
    ck = max(1, ck)
    close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ck, ck))
    mask_closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_k, iterations=1)
    dbg["mask_closed"] = mask_closed

    return mask_closed, dbg


def extract_curve_pixels(plot_bgr: np.ndarray, monotonic: str, preprocess_cfg: Dict[str, Any]) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """
    MVP extraction: per-x column pick representative y from mask pixels.
    monotonic influences selection only weakly; default uses median y for robustness.
    Returns points_px (N,2) as float32 and debug images.
    """
    mask, dbg = preprocess_plot_to_mask(plot_bgr, preprocess_cfg)
    H, W = mask.shape[:2]

    xs = []
    ys = []

    # For each column, find all y where mask is on
    for x in range(W):
        col = mask[:, x]
        yy = np.flatnonzero(col > 0)
        if yy.size == 0:
            continue

        # robust selection: median
        y = float(np.median(yy))

        # optional: if monotonic and you want bias
        # (kept minimal; median generally works best under noise)
        xs.append(float(x))
        ys.append(y)

    if len(xs) < 20:
        pts = np.zeros((0, 2), dtype=np.float32)
    else:
        pts = np.stack([np.array(xs, dtype=np.float32), np.array(ys, dtype=np.float32)], axis=1)

    return pts, dbg


def _largest_contiguous_run(xs_int: np.ndarray) -> Tuple[int, int]:
    """
    xs_int must be sorted unique ints.
    Returns indices (start_idx, end_idx_inclusive) of the largest contiguous run (diff==1).
    """
    if xs_int.size == 0:
        return 0, -1
    dif = np.diff(xs_int)
    breaks = np.where(dif != 1)[0]
    # segments: [0..break], [break+1..next_break], ...
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks, [xs_int.size - 1]])
    lengths = ends - starts + 1
    k = int(np.argmax(lengths))
    return int(starts[k]), int(ends[k])


def trim_and_resample_curve(points_px: np.ndarray,
                            axis_map: Dict[str, Any],
                            swap_xy: bool,
                            n_samples: int,
                            trim_cfg: Dict[str, Any]) -> Dict[str, Any]:
    if points_px.size == 0:
        return {
            "xy_raw": np.zeros((0, 2), dtype=np.float64),
            "xy_clean": np.zeros((0, 2), dtype=np.float64),
            "xy_resampled": np.zeros((0, 2), dtype=np.float64),
            "valid_range": None,
        }

    # sort by x
    pts = points_px[np.argsort(points_px[:, 0])]
    # collapse duplicate x by median y
    x_int = np.round(pts[:, 0]).astype(int)
    uniq_x = np.unique(x_int)
    x2 = []
    y2 = []
    for ux in uniq_x:
        sel = pts[x_int == ux]
        x2.append(float(ux))
        y2.append(float(np.median(sel[:, 1])))
    x2 = np.array(x2, dtype=np.float64)
    y2 = np.array(y2, dtype=np.float64)

    # trim: take largest contiguous run
    s_idx, e_idx = _largest_contiguous_run(uniq_x)
    if e_idx < s_idx:
        return {
            "xy_raw": np.zeros((0, 2), dtype=np.float64),
            "xy_clean": np.zeros((0, 2), dtype=np.float64),
            "xy_resampled": np.zeros((0, 2), dtype=np.float64),
            "valid_range": None,
        }

    x_run = x2[s_idx:e_idx + 1]
    y_run = y2[s_idx:e_idx + 1]

    # edge guard: trim edges until we have enough consecutive columns
    edge_guard = int(trim_cfg.get("edge_guard_cols", 10))
    if x_run.size > 2 * edge_guard + 5:
        x_run = x_run[edge_guard:-edge_guard]
        y_run = y_run[edge_guard:-edge_guard]

    # pixel -> unit
    px_to_unit = axis_map["px_to_unit"]
    xu = []
    yu = []
    for xp, yp in zip(x_run, y_run):
        a, b = px_to_unit(xp, yp)
        xu.append(a)
        yu.append(b)
    xu = np.array(xu, dtype=np.float64)
    yu = np.array(yu, dtype=np.float64)

    if swap_xy:
        xu, yu = yu, xu

    # sort by x in unit space (important after swap)
    order = np.argsort(xu)
    xu = xu[order]
    yu = yu[order]

    # drop any duplicate xu
    xu_u = []
    yu_u = []
    i = 0
    while i < xu.size:
        j = i + 1
        while j < xu.size and abs(xu[j] - xu[i]) < 1e-12:
            j += 1
        xu_u.append(xu[i])
        yu_u.append(float(np.median(yu[i:j])))
        i = j
    xu = np.array(xu_u, dtype=np.float64)
    yu = np.array(yu_u, dtype=np.float64)

    if xu.size < 10:
        xy_raw = np.stack([xu, yu], axis=1) if xu.size else np.zeros((0, 2), dtype=np.float64)
        return {
            "xy_raw": xy_raw,
            "xy_clean": xy_raw,
            "xy_resampled": xy_raw,
            "valid_range": (float(np.min(xu)), float(np.max(xu))) if xu.size else None,
        }

    # resample to fixed N (linear interpolation)
    n = int(n_samples)
    n = max(50, n)
    x_min = float(np.min(xu))
    x_max = float(np.max(xu))
    xr = np.linspace(x_min, x_max, n, dtype=np.float64)
    yr = np.interp(xr, xu, yu)

    xy_raw = np.stack([xu, yu], axis=1)
    xy_resampled = np.stack([xr, yr], axis=1)

    return {
        "xy_raw": xy_raw,
        "xy_clean": xy_raw,
        "xy_resampled": xy_resampled,
        "valid_range": (x_min, x_max),
    }


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    y_true = y_true.astype(np.float64)
    y_pred = y_pred.astype(np.float64)
    resid = y_true - y_pred
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2)) if y_true.size else 0.0
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    rmse = float(np.sqrt(np.mean(resid ** 2))) if y_true.size else 0.0
    denom = np.maximum(np.abs(y_true), 1e-9)
    max_rel = float(np.max(np.abs(resid) / denom)) if y_true.size else 0.0
    end_err = float(max(abs(resid[0]), abs(resid[-1]))) if y_true.size >= 2 else 0.0
    return {"r2": r2, "rmse": rmse, "max_rel_err": max_rel, "endpoint_err": end_err}


def fit_polynomial_curve(xy: np.ndarray, degree_default: int, degree_min: int) -> Dict[str, Any]:
    if xy.size == 0 or xy.shape[0] < 20:
        return {
            "degree_used": None,
            "coeff_power": [0.0] * 7,
            "metrics": {"r2": 0.0, "rmse": 0.0, "max_rel_err": 0.0, "endpoint_err": 0.0},
            "status": "fail",
        }

    x = xy[:, 0].astype(np.float64)
    y = xy[:, 1].astype(np.float64)

    deg0 = int(degree_default)
    deg_min = int(degree_min)
    deg0 = min(max(deg0, 1), 6)
    deg_min = min(max(deg_min, 1), deg0)

    # acceptance thresholds (MVP; tune later)
    TH_R2 = 0.995
    TH_MAX_REL = 0.03  # 3%
    TH_END_ERR = 0.08  # absolute, depends on chart scale; relax if needed

    best = None
    for d in range(deg0, deg_min - 1, -1):
        try:
            p = np.poly1d(np.polyfit(x, y, d))
        except Exception:
            continue

        y_hat = p(x)
        m = _metrics(y, y_hat)

        # build fixed length c0..c6 (power basis, ascending)
        # np.poly1d stores descending powers: [a_d ... a0]
        desc = p.c.astype(np.float64).tolist()
        # convert to ascending with padding to degree 6
        asc = [0.0] * 7
        # desc corresponds to powers d..0
        for i, coef in enumerate(desc):
            power = d - i
            if 0 <= power <= 6:
                asc[power] = float(coef)

        status = "pass" if (m["r2"] >= TH_R2 and m["max_rel_err"] <= TH_MAX_REL and m["endpoint_err"] <= TH_END_ERR) else "warn"
        cand = {"degree_used": d, "coeff_power": asc, "metrics": m, "status": status, "poly_desc": desc}
        best = cand if best is None else best

        if status == "pass":
            best = cand
            break

        # keep the best warn by r2 then max_rel_err
        if best is None:
            best = cand
        else:
            b = best
            if (m["r2"] > b["metrics"]["r2"]) or (m["r2"] == b["metrics"]["r2"] and m["max_rel_err"] < b["metrics"]["max_rel_err"]):
                best = cand

    if best is None:
        return {
            "degree_used": None,
            "coeff_power": [0.0] * 7,
            "metrics": {"r2": 0.0, "rmse": 0.0, "max_rel_err": 0.0, "endpoint_err": 0.0},
            "status": "fail",
        }

    # If warn is too poor, mark fail
    if best["metrics"]["r2"] < 0.97:
        best["status"] = "fail"

    return {
        "degree_used": best["degree_used"],
        "coeff_power": best["coeff_power"],
        "metrics": best["metrics"],
        "status": best["status"],
    }


def assemble_coeff_bundle(part_no: str, pdf_sha1: str, fit_results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out = {
        "part_no": part_no,
        "pdf_sha1": pdf_sha1,
        "meta": {"degree_used": {}, "metrics": {}, "status": {}},
    }

    # Always output fixed fields 0..6 for all known chart IDs (fill missing with 0)
    for cid in ["FIV", "FIL", "FTV", "FTL"]:
        fr = fit_results.get(cid)
        coeff = [0.0] * 7 if fr is None else list(fr["coeff_power"])
        for k in range(7):
            out[f"{cid}_{k}"] = float(coeff[k])
        out["meta"]["degree_used"][cid] = None if fr is None else fr.get("degree_used")
        out["meta"]["metrics"][cid] = {} if fr is None else fr.get("metrics", {})
        out["meta"]["status"][cid] = "missing" if fr is None else fr.get("status", "unknown")

    return out


def overlay_points(plot_bgr: np.ndarray, points_px: np.ndarray, color=(0, 0, 255), radius=1) -> np.ndarray:
    out = plot_bgr.copy()
    if points_px.size == 0:
        return out
    for x, y in points_px:
        cv2.circle(out, (int(round(x)), int(round(y))), radius, color, -1)
    return out


def overlay_fit_unit_curve_on_plot(plot_bgr: np.ndarray,
                                  axis_map: Dict[str, Any],
                                  fit: Dict[str, Any],
                                  swap_xy: bool,
                                  color=(255, 0, 0),
                                  thickness=2) -> np.ndarray:
    """
    Draw fitted curve by sampling x across plot width, mapping unit->px (inverse mapping).
    We only have px->unit mapping; for MVP we'll do inverse by linear mapping derived from axis_map.
    """
    out = plot_bgr.copy()
    H, W = axis_map["H"], axis_map["W"]
    x_min, x_max = axis_map["x_min"], axis_map["x_max"]
    y_min, y_max = axis_map["y_min"], axis_map["y_max"]

    if fit.get("degree_used") is None:
        return out

    coeff = fit["coeff_power"]  # c0..c6
    def poly(x):
        # Horner
        y = 0.0
        for p in range(6, -1, -1):
            y = y * x + coeff[p]
        return y

    # unit->px linear inverse
    def unit_to_px(xu, yu):
        # xu in [x_min,x_max] -> x_px in [0,W-1]
        x_px = (xu - x_min) / (x_max - x_min) * max(W - 1, 1)
        # yu in [y_min,y_max], pixel y top=0 => y_px = (y_max - yu)/(y_max-y_min)*(H-1)
        y_px = (y_max - yu) / (y_max - y_min) * max(H - 1, 1)
        return x_px, y_px

    xs = np.linspace(x_min, x_max, 400)
    pts = []
    for xu in xs:
        yu = poly(float(xu))
        # If swap_xy, our model is y(x) where x is actually y-axis in original plot; still draw in plot space:
        # swap_xy means unit axes were swapped before fit, so to draw back, swap back here.
        if swap_xy:
            # In fit space: x_fit = y_plot_unit, y_fit = x_plot_unit
            x_plot_unit = yu
            y_plot_unit = xu
        else:
            x_plot_unit = xu
            y_plot_unit = yu

        x_px, y_px = unit_to_px(x_plot_unit, y_plot_unit)
        if 0 <= x_px < W and 0 <= y_px < H:
            pts.append((int(round(x_px)), int(round(y_px))))

    if len(pts) >= 2:
        cv2.polylines(out, [np.array(pts, dtype=np.int32)], isClosed=False, color=color, thickness=thickness)
    return out


def save_outputs(prefix: str,
                 page_image: np.ndarray,
                 regions: Dict[str, Any],
                 dbg_imgs: Dict[str, np.ndarray],
                 pts_px: np.ndarray,
                 curve: Dict[str, Any],
                 fit: Dict[str, Any],
                 bundle: Dict[str, Any]) -> None:
    # page overlays
    overlay = draw_bboxes_overlay(page_image, regions["roi_bbox"], regions["plot_bbox"])
    cv2.imwrite(f"{prefix}_page_overlay.png", overlay)
    cv2.imwrite(f"{prefix}_roi.png", regions["roi"])
    cv2.imwrite(f"{prefix}_plot.png", regions["plot"])

    # preprocess debug images
    for k, im in dbg_imgs.items():
        if im is None:
            continue
        out_path = f"{prefix}_{k}.png"
        cv2.imwrite(out_path, im)

    # traced overlay
    traced = overlay_points(regions["plot"], pts_px, color=(0, 0, 255), radius=1)
    cv2.imwrite(f"{prefix}_traced_overlay.png", traced)

    # fit overlay
    fit_overlay = overlay_fit_unit_curve_on_plot(regions["plot"], build_axis_mapping(regions["plot"].shape[:2], {"x_min": 0, "x_max": 1, "y_min": 0, "y_max": 1}),
                                                 {"degree_used": None, "coeff_power": [0.0]*7}, False)
    # Actually draw with real axis map from curve stage (we don't have it here), so we redraw properly below in run.
    # (This placeholder is overwritten by caller if they pass correct overlay.)

    # residual plot (simple: save as text for MVP)
    # Save json bundle
    with open(f"{prefix}_coeff_bundle.json", "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)

    # Save fit info
    with open(f"{prefix}_fit.json", "w", encoding="utf-8") as f:
        json.dump(fit, f, ensure_ascii=False, indent=2)

    # Save curve points
    xy = curve.get("xy_resampled")
    if isinstance(xy, np.ndarray) and xy.size:
        np.savetxt(f"{prefix}_xy_resampled.csv", xy, delimiter=",", header="x,y", comments="")


def run_one_chart(pdf_path: str,
                  template: Dict[str, Any],
                  chart_id: str,
                  dpi: int,
                  prefix: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    chart_cfg = template["charts"][chart_id]
    page_index = int(template["pdf_pages"][chart_id])

    page_image = render_pdf_page(pdf_path, page_index, dpi=dpi)

    regions = crop_chart_regions(page_image, chart_cfg)
    page_overlay = draw_bboxes_overlay(page_image, regions["roi_bbox"], regions["plot_bbox"])
    cv2.imwrite(f"{prefix}_{chart_id}_page_overlay.png", page_overlay)
    cv2.imwrite(f"{prefix}_{chart_id}_plot.png", regions["plot"])

    axis_map = build_axis_mapping(regions["plot"].shape[:2], chart_cfg["axis"])

    pts_px, dbg_imgs = extract_curve_pixels(
        plot_bgr=regions["plot"],
        monotonic=chart_cfg["monotonic"],
        preprocess_cfg=chart_cfg.get("preprocess", {}),
    )

    curve = trim_and_resample_curve(
        points_px=pts_px,
        axis_map=axis_map,
        swap_xy=bool(chart_cfg.get("swap_xy", False)),
        n_samples=int(chart_cfg.get("n_samples", 200)),
        trim_cfg=chart_cfg.get("trim", {}),
    )

    fit = fit_polynomial_curve(
        xy=curve["xy_resampled"],
        degree_default=int(chart_cfg.get("degree_default", 6)),
        degree_min=int(chart_cfg.get("degree_min", 4)),
    )

    # traced overlay
    traced = overlay_points(regions["plot"], pts_px, color=(0, 0, 255), radius=1)
    cv2.imwrite(f"{prefix}_{chart_id}_traced_overlay.png", traced)

    # fit overlay (use real axis_map and swap)
    fit_ov = overlay_fit_unit_curve_on_plot(
        plot_bgr=regions["plot"],
        axis_map=axis_map,
        fit=fit,
        swap_xy=bool(chart_cfg.get("swap_xy", False)),
        color=(255, 0, 0),
        thickness=2,
    )
    cv2.imwrite(f"{prefix}_{chart_id}_fit_overlay.png", fit_ov)

    # dump preprocess debug
    for k, im in dbg_imgs.items():
        if im is None:
            continue
        cv2.imwrite(f"{prefix}_{chart_id}_{k}.png", im)

    # save curve points
    if isinstance(curve.get("xy_resampled"), np.ndarray) and curve["xy_resampled"].size:
        np.savetxt(f"{prefix}_{chart_id}_xy_resampled.csv", curve["xy_resampled"], delimiter=",", header="x,y", comments="")

    # save fit info
    with open(f"{prefix}_{chart_id}_fit.json", "w", encoding="utf-8") as f:
        json.dump(fit, f, ensure_ascii=False, indent=2)

    return fit, {"page_index": page_index, "axis_map": {k: axis_map[k] for k in ["x_min","x_max","y_min","y_max","x_unit","y_unit"]}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default="STW8A2PD-H0.pdf")
    ap.add_argument("--template", default="template_xxx.json")
    ap.add_argument("--dpi", type=int, default=350)
    ap.add_argument("--charts", default=",".join(CHART_IDS_DEFAULT), help="comma list: FIL,FIV,FTL,FTV")
    ap.add_argument("--prefix", default="mvp1", help="output filename prefix (no subfolders)")
    args = ap.parse_args()

    pdf_path = args.pdf
    tpl_path = args.template
    tpl = load_template(tpl_path)
    sha1 = compute_sha1(pdf_path)

    charts = [c.strip().upper() for c in args.charts.split(",") if c.strip()]
    fit_results = {}
    meta = {}

    for cid in charts:
        if cid not in tpl["charts"] or cid not in tpl["pdf_pages"]:
            raise ValueError(f"chart_id {cid} not found in template")
        fit, m = run_one_chart(pdf_path, tpl, cid, args.dpi, args.prefix)
        fit_results[cid] = fit
        meta[cid] = m

    bundle = assemble_coeff_bundle(tpl["part_no"], sha1, fit_results)
    bundle["meta"]["chart_meta"] = meta

    with open(f"{args.prefix}_coeff_bundle.json", "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)

    print("DONE")
    print(f"part_no: {tpl['part_no']}")
    print(f"pdf_sha1: {sha1}")
    for cid in charts:
        fr = fit_results[cid]
        print(f"{cid}: status={fr.get('status')} degree={fr.get('degree_used')} metrics={fr.get('metrics')}")
    print(f"bundle: {args.prefix}_coeff_bundle.json")


if __name__ == "__main__":
    main()
