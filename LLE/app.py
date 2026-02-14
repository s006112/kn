from decimal import Decimal, ROUND_HALF_UP
import traceback

from flask import Flask, render_template, request

from algorithm import (
    process_led_candidates,
    build_sorted_candidates_for_search,
    build_candidate_costs_for_config,
)
import db

app = Flask(__name__)
app.secret_key = "lle_phase_autosubmit_secret_key"


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


def _cost_usd(*, total_leds: float, unit_usd: float, smt_cost_rmb: float, usd_rate: float):
    led_cost_usd = total_leds * unit_usd if (total_leds > 0 and unit_usd > 0) else 0.0
    smt_cost_usd = total_leds * smt_cost_rmb / max(usd_rate, 1e-9) if total_leds > 0 else 0.0
    return led_cost_usd, smt_cost_usd, (led_cost_usd + smt_cost_usd)


def _cost_rmb(*, total_leds: float, unit_rmb: float, smt_cost_rmb: float):
    led_cost_rmb = total_leds * unit_rmb if (total_leds > 0 and unit_rmb > 0) else 0.0
    smt_cost_rmb_total = total_leds * smt_cost_rmb if total_leds > 0 else 0.0
    return led_cost_rmb, smt_cost_rmb_total, (led_cost_rmb + smt_cost_rmb_total)


def _extract_lm_test_overrides(data: dict) -> dict[int, float]:
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
    # -------------------- DB options (always) --------------------
    connection_status = "Failed"
    error_message = ""
    cct_options: list[float] = []
    cri_options: list[float] = []

    try:
        pairs = db.fetch_distinct_cct_cri()
        # pairs can be list[Row] or list[tuple]; support both
        cct_values = []
        cri_values = []
        for p in pairs:
            try:
                cct_values.append(p[0])
                cri_values.append(p[1])
            except Exception:
                # sqlite3.Row with keys
                cct_values.append(p.get("CCT"))
                cri_values.append(p.get("CRI"))
        cct_options = sorted({v for v in cct_values if v is not None})
        cri_options = sorted({v for v in cri_values if v is not None})
        connection_status = "Success"
    except Exception as e:
        connection_status = "Failed"
        error_message = str(e)
        traceback.print_exc()

    # -------------------- Defaults (GET baseline) --------------------
    defaults = {
        "target_cct": 4000.0,
        "target_cri": 80.0,
        "target_lumen": 5000.0,
        "target_efficacy": 125.0,
        "optical_transmission": 80.0,
        "power_efficiency": 85.0,
        "junction_temp": 65.0,
        "v_chain_max": 50.0,
        "smt_cost_rmb": 0.01,
        "usd_rate": 7.00,
    }

    # -------------------- Read + validate --------------------
    validation_errors: list[str] = []
    params = dict(defaults)
    lm_test_overrides: dict[int, float] = {}

    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        lm_test_overrides = _extract_lm_test_overrides(data)

        # required fields: match the template names
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

        # if validated -> overwrite defaults
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
            # keep "best-effort" echo back: use provided values when parseable
            for k in defaults:
                if k in data:
                    params[k] = to_float(data[k], defaults[k])

    # -------------------- Derive targets --------------------
    optical_rate = params["optical_transmission"] / 100.0
    power_rate = params["power_efficiency"] / 100.0
    combined = optical_rate * power_rate

    target_led_lumen = (params["target_lumen"] / optical_rate) if optical_rate > 0 else 0.0
    target_led_efficacy = (params["target_efficacy"] / combined) if combined > 0 else 0.0

    # -------------------- Query + algorithm (POST only, valid only) --------------------
    led_candidates = []
    led_config_solutions = {}
    candidate_count = 0

    sorted_candidates_display = []
    candidate_costs_display = []

    if request.method == "POST" and (not validation_errors) and params["target_cct"] > 0 and params["target_cri"] > 0:
        try:
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
            candidate_count = len(led_candidates)

            if candidate_count > 0:
                smt_cost_rmb_f = params["smt_cost_rmb"]
                usd_rate_f = params["usd_rate"]
                target_efficacy_f = params["target_efficacy"]

                sorted_candidates = build_sorted_candidates_for_search(
                    led_candidates, led_config_solutions, smt_cost_rmb_f, usd_rate_f
                )
                candidate_costs = build_candidate_costs_for_config(
                    led_candidates, led_config_solutions, smt_cost_rmb_f, usd_rate_f
                )

                # summary table
                for item in sorted_candidates:
                    idx = item["index"]
                    cand = item["candidate"]
                    first_solution = (led_config_solutions.get(idx) or [None])[0]
                    total_leds = float(first_solution.get("total_leds", 0)) if first_solution else 0.0

                    lm_per_led = float(cand.get("lumen_at_target_Tj_target_if", 0) or 0.0)
                    fixture_lm = (lm_per_led * total_leds * optical_rate) if (lm_per_led > 0 and total_leds > 0 and optical_rate > 0) else 0.0
                    input_power = (fixture_lm / target_efficacy_f) if (fixture_lm > 0 and target_efficacy_f > 0) else 0.0

                    unit_usd = float(cand.get("USD", 0) or 0.0)
                    led_cost_usd, smt_cost_usd, total_cost_usd = _cost_usd(
                        total_leds=total_leds, unit_usd=unit_usd, smt_cost_rmb=smt_cost_rmb_f, usd_rate=usd_rate_f
                    )

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

                # configuration table
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
            # keep both: message for UI + traceback for debugging
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
