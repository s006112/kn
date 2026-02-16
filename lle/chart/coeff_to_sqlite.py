#!/usr/bin/env python3
# step 5: fit_to_coeffs.py -> coeff_to_sqlite.py

import sqlite3
import json
import argparse
import shutil
from pathlib import Path
from path_config import load_chart_runtime


def reverse_coeff(coeff_power):
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

    if not BUNDLE.exists():
        raise RuntimeError("CoeffBundle.json not found")

    bundle = json.load(open(BUNDLE))

    if not SOURCE_DB.exists():
        raise RuntimeError("Source DB not found")

    if args.apply:
        print("[BUILD] Copying source DB to new DB...")
        shutil.copyfile(SOURCE_DB, TARGET_DB)
        db_path = TARGET_DB
    else:
        print("[DRY-RUN] Would build new DB:", TARGET_DB)
        db_path = SOURCE_DB  # dummy for structure check

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT * FROM LED_CoE WHERE Model=?",
        (args.model,)
    ).fetchall()

    if not rows:
        print(f"[ERROR] No rows found for model {args.model}")
        return

    print(f"[INFO] Found {len(rows)} rows for model {args.model}")

    for row in rows:
        row_id = row["ID"]
        update_fields = {}

        for prefix in ["FIV", "FIL", "FTL", "FTV"]:
            if prefix not in bundle:
                continue

            coeff_sqlite = reverse_coeff(bundle[prefix]["coeff_power"])

            for k in range(7):
                update_fields[f"{prefix}_{k}"] = 0.0

            for k, val in enumerate(coeff_sqlite):
                update_fields[f"{prefix}_{k}"] = float(val)

        set_clause = ", ".join([f"{k}=?" for k in update_fields])
        sql = f"UPDATE LED_CoE SET {set_clause} WHERE ID=?"

        values = list(update_fields.values())
        values.append(row_id)

        if args.apply:
            cur.execute(sql, values)
        else:
            print(f"[DRY-RUN] Would update row ID={row_id}")

    if args.apply:
        conn.commit()
        print("[OK] New database built:", TARGET_DB)

    conn.close()


if __name__ == "__main__":
    main()
