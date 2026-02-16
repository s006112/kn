#!/usr/bin/env python3
# step 4 fit_to_coeffs.py

import json
from pathlib import Path
from typing import Dict, Any

FIT_KEYS = {"x_min", "x_max", "y_min", "y_max", "swap_xy"}

# ==========================
# CORE
# ==========================

def main():

    BASE_DIR = Path(__file__).resolve().parent
    DEBUG_DIR = (BASE_DIR / "../../data/chart/raw/debug").resolve()
    COEFF_DIR = (BASE_DIR / "../../data/chart/raw/").resolve()
    CONFIG_PATH = BASE_DIR / "chart_config.json"

    with open(CONFIG_PATH, "r") as f:
        chart_config: Dict[str, Dict[str, Any]] = json.load(f)["charts"]

    fit_ready_config = {
        key: val
        for key, val in chart_config.items()
        if FIT_KEYS.issubset(val)
    }

    bundle: Dict[str, Any] = {}

    for chart_id, cfg in fit_ready_config.items():

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
