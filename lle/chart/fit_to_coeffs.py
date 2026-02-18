#!/usr/bin/env python3
# step 4 fit_to_coeffs.py

import json
from pathlib import Path
from typing import Dict, Any
from path_config import load_chart_runtime


def main():
    BASE_DIR = Path(__file__).resolve().parent
    RAW_DIR, DEBUG_DIR, config = load_chart_runtime(BASE_DIR)
    chart_config: Dict[str, Dict[str, Any]] = config["charts"]

    updated = False
    for chart_id, cfg in chart_config.items():
        stem = Path(cfg["filename"]).stem
        fit_path = DEBUG_DIR / f"{stem}_fit_result.json"

        if not fit_path.exists():
            print(f"[SKIP] {chart_id} no fit result")
            continue

        with open(fit_path, "r") as f:
            fit_data = json.load(f)

        # coefficients
        cfg["degree"] = fit_data["degree_used"]
        cfg["coeff_power"] = fit_data["coeff_power"]
        cfg["status"] = fit_data.get("status", "ok")

        # IMPORTANT: persist polynomial input/output domain AFTER swap_xy mapping
        # Do NOT overwrite cfg["domain"] (chart axis mapping domain). Keep them separate.
        # https://chatgpt.com/c/6996426c-7748-839e-88e5-6c1e2997aa74
        if "domain" in fit_data and isinstance(fit_data["domain"], dict):
            cfg["poly_domain"] = fit_data["domain"]

        updated = True
        print(f"[OK] {chart_id} updated")

    if not updated:
        print("No coefficients updated.")
        return

    out_path = RAW_DIR / "chart_config.json"
    with open(out_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"{out_path.name} written.")


if __name__ == "__main__":
    main()
