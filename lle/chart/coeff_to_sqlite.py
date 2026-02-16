#!/usr/bin/env python3
# step 5: coeff_to_sqlite.py

import sqlite3
import json
import argparse
from pathlib import Path
from path_config import load_chart_runtime


def reverse_coeff(coeff_power):
    # JSON: [a_d ... a_0]  →  sqlite: 0..d
    return list(reversed(coeff_power))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    BASE_DIR = Path(__file__).resolve().parent
    CONFIG_PATH = BASE_DIR / "chart_config.json"
    RAW_DIR, _, _ = load_chart_runtime(BASE_DIR, CONFIG_PATH)

    SOURCE_DB = BASE_DIR.parent / "led_coe_fallback.sqlite3"
    TARGET_DB = BASE_DIR.parent / "led_parameters.sqlite3"
    BUNDLE = RAW_DIR / "CoeffBundle.json"

    if not args.apply:
        print("[DRY-RUN] Would build comparison DB:", TARGET_DB)
        return

    if not SOURCE_DB.exists():
        raise RuntimeError("Source DB not found")

    if not BUNDLE.exists():
        raise RuntimeError("CoeffBundle.json not found")

    bundle = json.load(open(BUNDLE))

    # --- open source ---
    src = sqlite3.connect(SOURCE_DB)
    src.row_factory = sqlite3.Row
    src_cur = src.cursor()

    rows = src_cur.execute(
        "SELECT * FROM LED_CoE WHERE Model=?",
        (args.model,)
    ).fetchall()

    if not rows:
        raise RuntimeError(f"Model {args.model} not found in source DB")

    print(f"[INFO] Found {len(rows)} rows for model {args.model}")

    # --- recreate target DB ---
    if TARGET_DB.exists():
        TARGET_DB.unlink()

    tgt = sqlite3.connect(TARGET_DB)
    tgt.row_factory = sqlite3.Row
    tgt_cur = tgt.cursor()

    # copy table schema
    schema_row = src_cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='LED_CoE'"
    ).fetchone()

    if not schema_row:
        raise RuntimeError("LED_CoE table not found in source DB")

    tgt_cur.execute(schema_row[0])

    # --- insert _old and _new ---
    for row in rows:
        row_dict = dict(row)

        # ===== OLD =====
        old_row = row_dict.copy()
        old_row["Model"] = f"{args.model}_old"
        old_row.pop("ID", None)  # remove primary key

        columns_old = ", ".join(old_row.keys())
        placeholders_old = ", ".join(["?"] * len(old_row))

        tgt_cur.execute(
            f"INSERT INTO LED_CoE ({columns_old}) VALUES ({placeholders_old})",
            list(old_row.values())
        )

        # ===== NEW =====
        new_row = row_dict.copy()
        new_row["Model"] = f"{args.model}_new"

        for prefix in ["FIV", "FIL", "FTL", "FTV"]:
            if prefix not in bundle:
                continue

            coeff_sqlite = reverse_coeff(bundle[prefix]["coeff_power"])

            for k in range(7):
                new_row[f"{prefix}_{k}"] = 0.0

            for k, val in enumerate(coeff_sqlite):
                new_row[f"{prefix}_{k}"] = float(val)

        new_row.pop("ID", None)  # remove primary key

        columns_new = ", ".join(new_row.keys())
        placeholders_new = ", ".join(["?"] * len(new_row))

        tgt_cur.execute(
            f"INSERT INTO LED_CoE ({columns_new}) VALUES ({placeholders_new})",
            list(new_row.values())
        )

    tgt.commit()
    src.close()
    tgt.close()

    print("[OK] Built:", TARGET_DB)
    print("Contains:")
    print("  -", args.model + "_old")
    print("  -", args.model + "_new")


if __name__ == "__main__":
    main()
