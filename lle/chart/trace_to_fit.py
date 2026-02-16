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
        best = None

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

                best = {
                    "degree_used": deg,
                    "coeff_power": coeff.tolist(),
                    "metrics": {
                        "r2": float(r2),
                        "rmse": float(rmse),
                        "endpoint_err": float(endpoint_err),
                        "valid_points": int(x_unit.size)
                    },
                    "status": "ok"
                }

                break

            except Exception:
                continue

        if best is None:
            print(f"[FAIL] {stem} fit failed")
            continue

        out_path = jf.with_name(stem + "_fit_result.json")
        with open(out_path, "w") as f:
            json.dump(best, f, indent=2)

        print(f"[OK] {stem} deg={best['degree_used']} "
              f"R2={best['metrics']['r2']:.4f} "
              f"RMSE={best['metrics']['rmse']:.6f}")

    print("Step-3 done.")


if __name__ == "__main__":
    main()
