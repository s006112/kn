#!/usr/bin/env python3
# trace_to_fit.py

import json
import numpy as np
from pathlib import Path
from typing import Dict, Any

# ==========================
# CHART CONFIG
# ==========================
CHART_CONFIG: Dict[str, Dict[str, Any]] = {
    "FIL": {
        "filename": "9f4c7cf6-d991-4242-a6b4-debd4ff71ed3.png",
        "plot_bbox": [73, 50, 591, 417],
        "x_min": 0.0, "x_max": 300.0,
        "y_min": 0.0, "y_max": 3.5,
        "swap_xy": False,
    },
    "FIV": {
        "filename": "Weixin Image_20260214170155_250_28.png",
        "plot_bbox": [77, 45, 587, 405],
        "x_min": 2.5, "x_max": 3.1,
        "y_min": 0.0, "y_max": 300.0,
        "swap_xy": True,
    },
}

MAX_DEGREE = 6
MIN_DEGREE = 4

# ==========================
# CORE
# ==========================

def main():

    BASE_DIR = Path(__file__).resolve().parent
    DEBUG_DIR = (BASE_DIR / "../../data/chart/raw/debug").resolve()

    json_files = sorted(DEBUG_DIR.glob("*_curve_points_px.json"))
    if not json_files:
        print("No trace json found.")
        return

    for jf in json_files:

        stem = jf.stem.replace("_curve_points_px", "")

        cfg = None
        chart_id = None

        for key, val in CHART_CONFIG.items():
            if val["filename"].startswith(stem) or stem.startswith(val["filename"].split(".")[0]):
                cfg = val
                chart_id = key
                break

        if cfg is None:
            continue

        with open(jf, "r") as f:
            data = json.load(f)

        xp = np.array(data["xp"], dtype=np.float64)
        yp = np.array(data["yp_raw"], dtype=np.float64)

        # 1️⃣ 过滤 NaN
        valid = np.isfinite(yp)
        xp = xp[valid]
        yp = yp[valid]

        if xp.size < MIN_DEGREE + 1:
            print(f"[FAIL] {stem} not enough valid points")
            continue

        W = data["trace_info"]["width"]
        H = data["trace_info"]["height"]

        # 2️⃣ 单位映射（轴决定比例）
        x_unit = cfg["x_min"] + (xp / (W - 1.0)) * (cfg["x_max"] - cfg["x_min"])
        y_unit = cfg["y_max"] - (yp / (H - 1.0)) * (cfg["y_max"] - cfg["y_min"])

        # 若需要交换
        if cfg["swap_xy"]:
            x_unit, y_unit = y_unit, x_unit

        # 3️⃣ 自动降阶拟合

        candidates = []

        for deg in range(MAX_DEGREE, MIN_DEGREE - 1, -1):

            try:
                coeff = np.polyfit(x_unit, y_unit, deg)
                y_pred = np.polyval(coeff, x_unit)

                residual = y_unit - y_pred
                rmse = np.sqrt(np.mean(residual**2))

                ss_tot = np.sum((y_unit - np.mean(y_unit))**2)
                ss_res = np.sum(residual**2)
                r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

                endpoint_err = abs(y_pred[0] - y_unit[0]) + \
                            abs(y_pred[-1] - y_unit[-1])

                candidates.append({
                    "degree": deg,
                    "coeff": coeff,
                    "rmse": rmse,
                    "r2": r2,
                    "endpoint_err": endpoint_err
                })

            except Exception:
                continue

        if not candidates:
            print(f"[FAIL] {stem} fit failed")
            continue

        # ==========================
        # 选择策略（最小复杂度版本）
        # 1. 先按 rmse 排序
        # 2. 若 rmse 差异 <5%，选更低阶
        # ==========================

        candidates.sort(key=lambda x: x["rmse"])

        best = candidates[0]

        for c in candidates[1:]:
            if abs(c["rmse"] - best["rmse"]) / best["rmse"] < 0.05:
                if c["degree"] < best["degree"]:
                    best = c

        result = {
            "degree_used": best["degree"],
            "coeff_power": best["coeff"].tolist(),
            "metrics": {
                "r2": float(best["r2"]),
                "rmse": float(best["rmse"]),
                "endpoint_err": float(best["endpoint_err"]),
                "valid_points": int(x_unit.size)
            },
            "candidates_eval": [
                {
                    "degree": c["degree"],
                    "rmse": float(c["rmse"]),
                    "r2": float(c["r2"]),
                    "endpoint_err": float(c["endpoint_err"])
                }
                for c in candidates
            ],
            "status": "ok"
        }

        out_path = jf.with_name(stem + "_fit_result.json")
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)

        print(f"[OK] {stem} deg={result['degree_used']} "
            f"R2={result['metrics']['r2']:.4f} "
            f"RMSE={result['metrics']['rmse']:.6f}")


    print("Step-3 done.")


if __name__ == "__main__":
    main()
