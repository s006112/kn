#!/usr/bin/env python3
# step 4 fit_to_coeffs.py

import json
from pathlib import Path
from typing import Dict, Any
from path_config import load_chart_runtime

FIT_KEYS = {"x_min", "x_max", "y_min", "y_max"}

# ==========================
# CORE
# ==========================

def main():

    BASE_DIR = Path(__file__).resolve().parent
    RAW_DIR, DEBUG_DIR, config = load_chart_runtime(BASE_DIR)
    chart_config: Dict[str, Dict[str, Any]] = config["charts"]

    fit_ready_config: Dict[str, Dict[str, Any]] = {}
    for key, val in chart_config.items():
        domain = val.get("domain")
        if not isinstance(domain, dict) or not FIT_KEYS.issubset(domain) or "swap_xy" not in val:
            continue
        fit_ready_config[key] = {**val, **domain}

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
            "domain": {
                "x_min": cfg["x_min"],
                "x_max": cfg["x_max"],
                "y_min": cfg["y_min"],
                "y_max": cfg["y_max"],
            },
            "swap_xy": cfg["swap_xy"],
            "status": "ok"
        }

        print(f"[OK] {chart_id} bundled")

    if not bundle:
        print("No coefficients bundled.")
        return

    out_path = RAW_DIR / f"{stem}.json"

    with open(out_path, "w") as f:
        json.dump(bundle, f, indent=2)

    print(f"{out_path.name} written.")


if __name__ == "__main__":
    main()
