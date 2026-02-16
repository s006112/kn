#!/usr/bin/env python3
# fit_to_coeffs.py

import json
from pathlib import Path
from typing import Dict, Any

# ==========================
# CHART CONFIG
# ==========================
CHART_CONFIG: Dict[str, Dict[str, Any]] = {
    "FIL": {
        "filename": "9f4c7cf6-d991-4242-a6b4-debd4ff71ed3.png",
        "x_min": 0.0, "x_max": 300.0,
        "y_min": 0.0, "y_max": 3.5,
        "swap_xy": False,
    },
    "FIV": {
        "filename": "Weixin Image_20260214170155_250_28.png",
        "x_min": 2.5, "x_max": 3.1,
        "y_min": 0.0, "y_max": 300.0,
        "swap_xy": True,
    },
}

# ==========================
# CORE
# ==========================

def main():

    BASE_DIR = Path(__file__).resolve().parent
    DEBUG_DIR = (BASE_DIR / "../../data/chart/raw/debug").resolve()
    COEFF_DIR = (BASE_DIR / "../../data/chart/raw/").resolve()

    bundle: Dict[str, Any] = {}

    for chart_id, cfg in CHART_CONFIG.items():

        stem = Path(cfg["filename"]).stem
        fit_path = DEBUG_DIR / f"{stem}_fit_result.json"

        if not fit_path.exists():
            print(f"[SKIP] {chart_id} no fit result")
            continue

        with open(fit_path, "r") as f:
            fit_data = json.load(f)

        bundle[chart_id] = {
            "degree": fit_data["degree_used"],
            "coeff_power": fit_data["coeff_power"],
            "x_domain_min": cfg["x_min"],
            "x_domain_max": cfg["x_max"],
            "y_domain_min": cfg["y_min"],
            "y_domain_max": cfg["y_max"],
            "swap_xy": cfg["swap_xy"],
            "status": "ok"
        }

        print(f"[OK] {chart_id} bundled")

    if not bundle:
        print("No coefficients bundled.")
        return

    out_path = COEFF_DIR / "CoeffBundle.json"

    with open(out_path, "w") as f:
        json.dump(bundle, f, indent=2)

    print("CoeffBundle.json written.")


if __name__ == "__main__":
    main()
