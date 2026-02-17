#!/usr/bin/env python3
"""Step 5: coeffs_to_sqlite.py append chart coefficients from chart_config.json into LED_CoE."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from path_config import load_chart_runtime

SERIES_RE = re.compile(r"^(?P<model>.+)_(?:FIV|FVI|FIL|FTL|FTV)(?:_|$)")
COEFF_SERIES = ("FIV", "FIL", "FTL", "FTV")


def infer_model_name(charts: dict) -> str:
    model_names: set[str] = set()
    for cfg in charts.values():
        filename = str(cfg.get("filename", ""))
        stem = Path(filename).stem
        m = SERIES_RE.match(stem)
        if m:
            model_names.add(m.group("model"))

    if not model_names:
        raise RuntimeError("No model name inferred from chart filenames")
    if len(model_names) != 1:
        raise RuntimeError(f"Multiple model names found: {sorted(model_names)}")
    return next(iter(model_names))


def cct_from_key(key: str) -> int:
    digits = "".join(ch for ch in str(key) if ch.isdigit())
    if not digits:
        raise RuntimeError(f"Invalid lm_test CCT key: {key}")
    return int(digits)


def coeff_value(coeff_power: list[float], power: int) -> float:
    degree = len(coeff_power) - 1
    idx = degree - power
    if idx < 0 or idx >= len(coeff_power):
        return 0.0
    return float(coeff_power[idx])


def main() -> None:
    BASE_DIR = Path(__file__).resolve().parent
    RAW_DIR, _, _ = load_chart_runtime(BASE_DIR)
    TARGET_DB = BASE_DIR.parent / "led_parameters.sqlite3"
    BUNDLE = RAW_DIR / "chart_config.json"

    if not BUNDLE.exists():
        raise RuntimeError(f"Bundle not found: {BUNDLE}")
    if not TARGET_DB.exists():
        raise RuntimeError(f"Target DB not found: {TARGET_DB}")

    with open(BUNDLE, "r") as f:
        config = json.load(f)

    charts = config.get("charts", {})
    if not isinstance(charts, dict) or not charts:
        raise RuntimeError("charts missing in chart_config.json")

    lm_test = config.get("lm_test", {})
    if not isinstance(lm_test, dict) or not lm_test:
        raise RuntimeError("lm_test missing in chart_config.json")

    model = infer_model_name(charts)
    cri = int(config.get("CRI", 0))
    if_max = float(config.get("If_max", 0.0))
    if_value = float(config.get("If", 0.0))
    usd = float(config.get("USD", 0.0))
    rmb = float(config.get("RMB", 0.0))
    quote = str(config.get("Quote_date", ""))
    vf = float(config.get("Vf", 0.0))
    tj = float(config.get("Tj", 0.0))

    base_coeffs: dict[str, float] = {}
    for series in COEFF_SERIES:
        chart_cfg = charts.get(series)
        if not isinstance(chart_cfg, dict):
            raise RuntimeError(f"{series} missing in charts")
        coeff_power = chart_cfg.get("coeff_power")
        if not isinstance(coeff_power, list) or not coeff_power:
            raise RuntimeError(f"{series}.coeff_power missing or invalid")
        if len(coeff_power) > 7:
            raise RuntimeError(f"{series}.coeff_power has degree > 6")
        for power in range(6, -1, -1):
            base_coeffs[f"{series}_{power}"] = coeff_value(coeff_power, power)

    rows: list[dict[str, object]] = []
    for cct_key, lm in sorted(lm_test.items(), key=lambda kv: cct_from_key(kv[0])):
        row = {
            "Model": model,
            "CCT": cct_from_key(cct_key),
            "CRI": cri,
            "lm_test": float(lm),
            "If_max": if_max,
            "If": if_value,
            "USD": usd,
            "RMB": rmb,
            "Quote": quote,
            "Vf": vf,
            "Tj": tj,
        }
        row.update(base_coeffs)
        rows.append(row)

    conn = sqlite3.connect(TARGET_DB)
    cur = conn.cursor()

    table_cols = [
        r[1]
        for r in cur.execute("PRAGMA table_info(LED_CoE)").fetchall()
        if r[1] != "ID"
    ]
    if not table_cols:
        raise RuntimeError("LED_CoE table not found or has no insertable columns")

    row_cols = sorted(rows[0].keys())
    if sorted(table_cols) != row_cols:
        raise RuntimeError(
            "Row keys do not match LED_CoE columns.\n"
            f"Row keys: {row_cols}\n"
            f"DB cols : {sorted(table_cols)}"
        )

    insert_cols = [c for c in table_cols if c in rows[0]]
    placeholders = ",".join(["?"] * len(insert_cols))
    sql = f"INSERT INTO LED_CoE ({','.join(insert_cols)}) VALUES ({placeholders})"

    cur.executemany(sql, [[row[c] for c in insert_cols] for row in rows])
    conn.commit()
    conn.close()

    print(f"[OK] Appended {len(rows)} rows for model={model} into {TARGET_DB}")


if __name__ == "__main__":
    main()
