from decimal import Decimal, ROUND_HALF_UP
import traceback

from flask import Flask, render_template, request

from algorithm import (
    process_led_candidates,
    build_sorted_candidates_for_search,
)
from pricing import build_presented_results
import db


app = Flask(__name__)
app.secret_key = "lle_phase_autosubmit_secret_key"


# -------------------------------------------------
# Utils
# -------------------------------------------------

def to_float(value, default=0.0) -> float:
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return float(default)


def number_format(value, decimals=0) -> str:
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
    raw = data.get(field, "")
    if raw is None or str(raw).strip() == "":
        errors.append(f"{label or field} invalid")
        return None

    x = to_float(raw, default=float("nan"))
    if x != x:
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


FIELD_SCHEMA = {
    "target_cct": dict(positive=True, label="Target CCT"),
    "target_cri": dict(positive=True, label="Target CRI"),
    "target_lumen": dict(positive=True, label="Target Luminaire Lumen Output"),
    "target_efficacy": dict(positive=True, label="Target Luminaire Efficacy (lm/W)"),
    "optical_transmission": dict(min_val=1, max_val=100, label="Optical Transmission (%)"),
    "power_efficiency": dict(min_val=1, max_val=100, label="Power Supply Efficiency (%)"),
    "junction_temp": dict(label="Junction Temperature (°C)"),
    "v_chain_max": dict(positive=True, label="Maximum LED Chain Voltage (V)"),
    "smt_cost_rmb": dict(min_val=0, label="SMT Cost (RMB)"),
    "usd_rate": dict(positive=True, label="USD Exchange Rate"),
}


def _extract_lm_test_overrides(data: dict) -> dict[int, float]:
    overrides: dict[int, float] = {}
    for key, raw_value in data.items():
        if not str(key).startswith("lm_test_"):
            continue
        try:
            row_id = int(str(key)[len("lm_test_") :])
            val = to_float(raw_value, default=float("nan"))
            if val == val:
                overrides[row_id] = float(val)
        except Exception:
            continue
    return overrides


# -------------------------------------------------
# Main Route
# -------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def main():

    connection_status = "Failed"
    error_message = ""
    cct_options = []
    cri_options = []

    try:
        pairs = db.fetch_distinct_cct_cri()
        cct_options = sorted({p[0] for p in pairs if p[0] is not None})
        cri_options = sorted({p[1] for p in pairs if p[1] is not None})
        connection_status = "Success"
    except Exception as e:
        error_message = str(e)
        traceback.print_exc()

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
        "usd_rate": 6.85,
    }

    params = dict(defaults)
    validation_errors = []
    lm_test_overrides = {}

    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        lm_test_overrides = _extract_lm_test_overrides(data)

        for field, rules in FIELD_SCHEMA.items():
            value = _require_float(data, field, errors=validation_errors, **rules)
            if value is not None:
                params[field] = value

    optical_rate = params["optical_transmission"] / 100.0
    power_rate = params["power_efficiency"] / 100.0
    combined = optical_rate * power_rate

    target_led_lumen = (
        params["target_lumen"] / optical_rate if optical_rate > 0 else 0.0
    )
    target_led_efficacy = (
        params["target_efficacy"] / combined if combined > 0 else 0.0
    )

    sorted_candidates_display = []
    candidate_costs_display = []
    candidate_count = 0
    led_config_solutions = {}

    if (
        request.method == "POST"
        and not validation_errors
        and params["target_cct"] > 0
        and params["target_cri"] > 0
    ):
        try:
            candidate_rows = db.fetch_candidates_by_cct_cri(
                params["target_cct"],
                params["target_cri"],
            )

            for row in candidate_rows:
                row["lm_test"] = to_float(row.get("lm_test", 0), 0)
                row_id = row.get("ID")
                if isinstance(row_id, int) and row_id in lm_test_overrides:
                    row["lm_test"] = lm_test_overrides[row_id]

            led_candidates, led_config_solutions = process_led_candidates(
                candidate_rows=candidate_rows,
                target_led_efficacy=target_led_efficacy,
                target_led_lumen=target_led_lumen,
                junction_temp=params["junction_temp"],
                v_chain_max=params["v_chain_max"],
            )

            sorted_candidates = build_sorted_candidates_for_search(
                led_candidates,
                led_config_solutions,
                params["smt_cost_rmb"],
                params["usd_rate"],
            )

            presented = build_presented_results(
                sorted_candidates=sorted_candidates,
                led_config_solutions=led_config_solutions,
                optical_rate=optical_rate,
                target_efficacy=params["target_efficacy"],
                smt_cost_rmb=params["smt_cost_rmb"],
                usd_rate=params["usd_rate"],
            )

            sorted_candidates_display = presented["sorted_candidates_display"]
            candidate_costs_display = presented["candidate_costs_display"]
            candidate_count = presented["candidate_count"]

        except Exception as e:
            error_message = f"Algorithm/Query error: {e}"
            traceback.print_exc()

    return render_template(
        "main.html",
        validation_errors=validation_errors,
        success_message="",
        connection_status=connection_status,
        error_message=error_message,
        cct_options=cct_options,
        cri_options=cri_options,
        target_led_lumen=target_led_lumen,
        target_led_efficacy=target_led_efficacy,
        led_config_solutions=led_config_solutions,
        candidate_count=candidate_count,
        sorted_candidates_display=sorted_candidates_display,
        candidate_costs_display=candidate_costs_display,
        **params,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
