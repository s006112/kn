"""
Own file name app.py

Responsibility
Flask entry module for the LED light engine (LLE) web workflow that validates user inputs, derives LED-side targets, queries candidate LEDs, runs configuration search, computes display cost metrics, and renders the main template with status and result tables.

Pipelines:
- request -> validate -> derive -> query -> search -> aggregate -> render

Invariants
- Request handling always returns `main.html` with a complete template context.
- Invalid POST input does not execute candidate search and is surfaced through `validation_errors`.
- Cost calculations are derived from the candidate rows and computed solutions without mutating algorithm or database modules.

Out of scope
- Persisting user-defined configurations.
- Implementing LED search algorithms or database storage logic.
- Frontend template structure and presentation behavior.
"""

from decimal import Decimal, ROUND_HALF_UP
import traceback

from flask import Flask, render_template, request

from algorithm import (
    process_led_candidates,
    build_sorted_candidates_for_search,
)
from pricing import _cost_usd, _cost_rmb
import db

app = Flask(__name__)
app.secret_key = "lle_phase_autosubmit_secret_key"


def to_float(value, default=0.0) -> float:
    """
    Purpose:
    Convert a value to float with comma-to-dot normalization and fallback default.
    Inputs:
    - value: Any value that may represent a numeric value.
    - default: Fallback numeric value used when conversion fails.
    Outputs:
    - float: Parsed float value or fallback default as float.
    """
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return float(default)


def number_format(value, decimals=0) -> str:
    """
    Purpose:
    Format numeric values using HALF_UP rounding with grouped thousands for UI display.
    Inputs:
    - value: Numeric-like value to format.
    - decimals: Number of decimal places to render.
    Outputs:
    - str: Formatted number string, or original string representation on failure.
    """
    try:
        dec = Decimal(str(value))
        if int(decimals) <= 0:
            rounded = dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            return f"{int(rounded):,}"
        quant = Decimal("1." + ("0" * int(decimals)))
        rounded = dec.quantize(quant, rounding=ROUND_HALF_UP)
        return f"{rounded:,.{int(decimals)}f}"
    except Exception:
        try:
            num = float(value)
            return f"{num:,.{int(decimals)}f}"
        except Exception:
            return str(value)


app.jinja_env.globals["number_format"] = number_format


def _require_float(
    data: dict,
    field: str,
    *,
    positive: bool = False,
    min_val: float | None = None,
    max_val: float | None = None,
    label: str | None = None,
    errors: list[str],
):
    """
    Purpose:
    Validate and parse a required numeric form field and append validation errors when constraints fail.
    Inputs:
    - data: Mapping of form field names to submitted values.
    - field: Target form field name.
    - positive: Whether value must be greater than zero.
    - min_val: Optional inclusive lower bound.
    - max_val: Optional inclusive upper bound.
    - label: Optional display label used in error messages.
    - errors: Mutable error list to append validation failures.
    Outputs:
    - float | None: Parsed float when valid, otherwise `None`.
    """
    raw = data.get(field, "")
    if raw is None or str(raw).strip() == "":
        errors.append(f"{label or field} invalid")
        return None

    x = to_float(raw, default=float("nan"))
    if x != x:  # NaN
        errors.append(f"{label or field} invalid")
        return None
    if positive and x <= 0:
        errors.append(f"{label or field} must be > 0")
        return None
    if min_val is not None and x < min_val:
        errors.append(f"{label or field} must be >= {min_val}")
        return None
    if max_val is not None and x > max_val:
        errors.append(f"{label or field} must be <= {max_val}")
        return None
    return float(x)


def _extract_lm_test_overrides(data: dict) -> dict[int, float]:
    """
    Purpose:
    Extract per-row `lm_test` overrides from form fields prefixed with `lm_test_`.
    Inputs:
    - data: Mapping of submitted form field names to values.
    Outputs:
    - dict[int, float]: Row ID to overridden lm_test value for valid entries.
    """
    overrides: dict[int, float] = {}
    for key, raw_value in data.items():
        if not str(key).startswith("lm_test_"):
            continue
        row_id_raw = str(key)[len("lm_test_") :].strip()
        if not row_id_raw:
            continue
        try:
            row_id = int(row_id_raw)
        except Exception:
            continue

        val = to_float(raw_value, default=float("nan"))
        if val != val:  # NaN
            continue
        overrides[row_id] = float(val)
    return overrides


@app.route("/", methods=["GET", "POST"])
def main():
    """
    Purpose:
    Handle GET and POST requests for the main page, including validation, candidate search, cost aggregation, and template rendering.
    Inputs:
    - None: Uses Flask request context (`request.method`, `request.form`).
    Outputs:
    - Response: Rendered `main.html` with form state, status, and result tables.
    """
    # Keep DB option loading outside POST logic so selectors always render.
    connection_status = "Failed"
    error_message = ""
    cct_options: list[float] = []
    cri_options: list[float] = []

    try:
        pairs = db.fetch_distinct_cct_cri()
        # Database adapters may return tuple-like rows or mapping-like rows.
        cct_values = []
        cri_values = []
        for p in pairs:
            try:
                cct_values.append(p[0])
                cri_values.append(p[1])
            except Exception:
                # Fallback preserves compatibility with key-addressable rows.
                cct_values.append(p.get("CCT"))
                cri_values.append(p.get("CRI"))
        cct_options = sorted({v for v in cct_values if v is not None})
        cri_options = sorted({v for v in cri_values if v is not None})
        connection_status = "Success"
    except Exception as e:
        connection_status = "Failed"
        error_message = str(e)
        traceback.print_exc()

    # Baseline values also seed POST echo-back when validation fails.
    defaults = {
        "target_cct": 4000,
        "target_cri": 80,
        "target_lumen": 5000,
        "target_efficacy": 125,
        "optical_transmission": 80,
        "power_efficiency": 85,
        "junction_temp": 65,
        "v_chain_max": 50,
        "smt_cost_rmb": 0.01,
        "usd_rate": 7.00,
    }

    # Validation errors gate algorithm execution to avoid partial computations.
    validation_errors: list[str] = []
    params = dict(defaults)
    lm_test_overrides: dict[int, float] = {}

    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        lm_test_overrides = _extract_lm_test_overrides(data)

        # Field names must stay aligned with template input names.
        target_cct = _require_float(data, "target_cct", positive=True, label="Target CCT", errors=validation_errors)
        target_cri = _require_float(data, "target_cri", positive=True, label="Target CRI", errors=validation_errors)
        target_lumen = _require_float(
            data, "target_lumen", positive=True, label="Target Luminaire Lumen Output", errors=validation_errors
        )
        target_efficacy = _require_float(
            data, "target_efficacy", positive=True, label="Target Luminaire Efficacy (lm/W)", errors=validation_errors
        )
        optical_transmission = _require_float(
            data, "optical_transmission", min_val=1, max_val=100, label="Optical Transmission (%)", errors=validation_errors
        )
        power_efficiency = _require_float(
            data, "power_efficiency", min_val=1, max_val=100, label="Power Supply Efficiency (%)", errors=validation_errors
        )
        junction_temp = _require_float(data, "junction_temp", label="Junction Temperature (°C)", errors=validation_errors)
        v_chain_max = _require_float(
            data, "v_chain_max", positive=True, label="Maximum LED Chain Voltage (V)", errors=validation_errors
        )
        smt_cost_rmb = _require_float(data, "smt_cost_rmb", min_val=0, label="SMT Cost (RMB)", errors=validation_errors)
        usd_rate = _require_float(data, "usd_rate", positive=True, label="USD Exchange Rate", errors=validation_errors)

        # Apply parsed POST values only when every required field passes checks.
        if not validation_errors:
            params.update(
                {
                    "target_cct": float(target_cct),
                    "target_cri": float(target_cri),
                    "target_lumen": float(target_lumen),
                    "target_efficacy": float(target_efficacy),
                    "optical_transmission": float(optical_transmission),
                    "power_efficiency": float(power_efficiency),
                    "junction_temp": float(junction_temp),
                    "v_chain_max": float(v_chain_max),
                    "smt_cost_rmb": float(smt_cost_rmb),
                    "usd_rate": float(usd_rate),
                }
            )
        else:
            # Preserve user input echo-back where parseable to support correction loops.
            for k in defaults:
                if k in data:
                    params[k] = to_float(data[k], defaults[k])

    # Derived targets convert luminaire-level requirements to LED-level constraints.
    optical_rate = params["optical_transmission"] / 100.0
    power_rate = params["power_efficiency"] / 100.0
    combined = optical_rate * power_rate

    target_led_lumen = (params["target_lumen"] / optical_rate) if optical_rate > 0 else 0.0
    target_led_efficacy = (params["target_efficacy"] / combined) if combined > 0 else 0.0

    # Query and search run only for validated POST requests.
    led_candidates = []
    led_config_solutions = {}
    candidate_count = 0

    sorted_candidates_display = []
    candidate_costs_display = []

    if request.method == "POST" and (not validation_errors) and params["target_cct"] > 0 and params["target_cri"] > 0:
        try:
            print(
                "LED_COUNT_PARAMS:",
                "target_cct=", params["target_cct"],
                "target_cri=", params["target_cri"],
                "target_lumen=", params["target_lumen"],
                "optical_transmission=", params["optical_transmission"],
                "power_efficiency=", params["power_efficiency"],
                "target_efficacy=", params["target_efficacy"],
                "junction_temp=", params["junction_temp"],
                "v_chain_max=", params["v_chain_max"],
                "smt_cost_rmb=", params["smt_cost_rmb"],
                "usd_rate=", params["usd_rate"],
                "target_led_lumen=", target_led_lumen,
                "target_led_efficacy=", target_led_efficacy,
            )
            candidate_rows = db.fetch_candidates_by_cct_cri(params["target_cct"], params["target_cri"])
            for row in candidate_rows:
                lm_test_value = to_float(row.get("lm_test", 0), 0)
                row["lm_test"] = lm_test_value

                row_id = row.get("ID")
                try:
                    row_id_int = int(row_id) if row_id is not None else None
                except Exception:
                    row_id_int = None

                if row_id_int is not None and row_id_int in lm_test_overrides:
                    row["lm_test"] = lm_test_overrides[row_id_int]

            led_candidates, led_config_solutions = process_led_candidates(
                candidate_rows=candidate_rows,
                target_led_efficacy=target_led_efficacy,
                target_led_lumen=target_led_lumen,
                junction_temp=params["junction_temp"],
                v_chain_max=params["v_chain_max"],
            )
            if led_candidates:
                smt_cost_rmb_f = params["smt_cost_rmb"]
                usd_rate_f = params["usd_rate"]
                target_efficacy_f = params["target_efficacy"]

                sorted_candidates = build_sorted_candidates_for_search(
                    led_candidates, led_config_solutions, smt_cost_rmb_f, usd_rate_f
                )
                # Single source of truth for both candidate and configuration sections.
                candidate_costs = sorted_candidates
                candidate_count = len(sorted_candidates)

                # Summary rows display first-solution economics and fixture-level metrics.
                for item in sorted_candidates:
                    idx = item["index"]
                    cand = item["candidate"]
                    first_solution = (led_config_solutions.get(idx) or [None])[0]
                    total_leds = float(item.get("total_leds", 0) or 0.0)

                    lm_per_led = float(cand.get("lumen_at_target_Tj_target_if", 0) or 0.0)
                    fixture_lm = (lm_per_led * total_leds * optical_rate) if (lm_per_led > 0 and total_leds > 0 and optical_rate > 0) else 0.0
                    input_power = (fixture_lm / target_efficacy_f) if (fixture_lm > 0 and target_efficacy_f > 0) else 0.0

                    led_cost_usd = float(item.get("led_cost_usd", 0) or 0.0)
                    smt_cost_usd = float(item.get("smt_cost_usd", 0) or 0.0)
                    total_cost_usd = float(item.get("total_cost_usd", 0) or 0.0)

                    sorted_candidates_display.append(
                        {
                            "index": idx,
                            "candidate": cand,
                            "fixture_lm": fixture_lm,
                            "input_power": input_power,
                            "total_led_count": int(total_leds) if first_solution else None,
                            "total_cost_usd": total_cost_usd if (led_cost_usd > 0 or smt_cost_usd > 0) else None,
                            "led_cost_usd": led_cost_usd,
                            "smt_cost_usd": smt_cost_usd,
                        }
                    )

                # Configuration rows preserve multiple topology solutions per candidate.
                for item in candidate_costs:
                    idx = item["index"]
                    cand = item["candidate"]
                    solutions_display = []

                    for sol in (led_config_solutions.get(idx) or [])[:10]:
                        total_leds = float(sol.get("total_leds", 0) or 0.0)

                        target_if_ma = float(cand.get("target_if", 0) or 0.0)
                        total_current = target_if_ma * float(sol.get("P", 0) or 0.0) if target_if_ma > 0 else 0.0

                        voltage = float(sol.get("V_chain", 0) or 0.0)
                        power_watts = (voltage * total_current / 1000.0) if (voltage > 0 and total_current > 0) else 0.0

                        unit_usd = float(cand.get("USD", 0) or 0.0)
                        unit_rmb = float(cand.get("RMB", 0) or 0.0)

                        led_cost_usd, smt_cost_usd, total_cost_usd = _cost_usd(
                            total_leds=total_leds,
                            unit_usd=unit_usd,
                            smt_cost_rmb=smt_cost_rmb_f,
                            usd_rate=usd_rate_f,
                        )
                        led_cost_rmb, smt_cost_rmb_total, total_cost_rmb = _cost_rmb(
                            total_leds=total_leds,
                            unit_rmb=unit_rmb,
                            smt_cost_rmb=smt_cost_rmb_f,
                        )

                        solutions_display.append(
                            {
                                "solution": sol,
                                "total_current": total_current,
                                "power_watts": power_watts,
                                "total_cost_usd": total_cost_usd,
                                "total_cost_rmb": total_cost_rmb,
                                "led_cost_usd": led_cost_usd,
                                "smt_cost_usd": smt_cost_usd,
                                "led_cost_rmb": led_cost_rmb,
                                "smt_cost_rmb_total": smt_cost_rmb_total,
                            }
                        )

                    candidate_costs_display.append(
                        {"index": idx, "candidate": cand, "solutions_display": solutions_display}
                    )

        except Exception as e:
            # Keep concise UI error text while retaining traceback in server logs.
            error_message = f"Algorithm/Query error: {e}"
            traceback.print_exc()

    return render_template(
        "main.html",
        validation_errors=validation_errors,
        success_message="",  # removed "store" phase; keep key to avoid template edits if still referenced
        # params for form echo
        target_cct=params["target_cct"],
        target_cri=params["target_cri"],
        target_lumen=params["target_lumen"],
        target_efficacy=params["target_efficacy"],
        optical_transmission=params["optical_transmission"],
        power_efficiency=params["power_efficiency"],
        junction_temp=params["junction_temp"],
        v_chain_max=params["v_chain_max"],
        smt_cost_rmb=params["smt_cost_rmb"],
        usd_rate=params["usd_rate"],
        # db status
        connection_status=connection_status,
        error_message=error_message,
        cct_options=cct_options,
        cri_options=cri_options,
        # results
        led_config_solutions=led_config_solutions,
        candidate_count=candidate_count,
        target_led_lumen=target_led_lumen,
        target_led_efficacy=target_led_efficacy,
        sorted_candidates_display=sorted_candidates_display,
        candidate_costs_display=candidate_costs_display,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
