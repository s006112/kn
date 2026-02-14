from decimal import Decimal, ROUND_HALF_UP
import traceback

from flask import Flask, render_template, request, session

from algorithm import (
    process_led_candidates,
    build_sorted_candidates_for_search,
    build_candidate_costs_for_config,
)
import db

app = Flask(__name__)
app.secret_key = "lle_phase_10_secret_key"


def to_float(value, default=0.0):
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return float(default)


def number_format(value, decimals=0):
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
    field: str,
    *,
    positive: bool = False,
    min_val: float | None = None,
    max_val: float | None = None,
    label: str | None = None,
    errors: list[str],
):
    raw = request.values.get(field, "")
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
    led_cost_usd = total_leds * unit_usd if unit_usd > 0 else 0.0
    smt_cost_usd = total_leds * smt_cost_rmb / max(usd_rate, 1e-9) if total_leds > 0 else 0.0
    return led_cost_usd, smt_cost_usd, (led_cost_usd + smt_cost_usd)


def _cost_rmb(*, total_leds: float, unit_rmb: float, smt_cost_rmb: float):
    led_cost_rmb = total_leds * unit_rmb if unit_rmb > 0 else 0.0
    smt_cost_rmb_total = total_leds * smt_cost_rmb if total_leds > 0 else 0.0
    return led_cost_rmb, smt_cost_rmb_total, (led_cost_rmb + smt_cost_rmb_total)


@app.route("/", methods=["GET", "POST"])
def main():
    form_submitted = request.method == "POST" and "calculate_params" in request.form
    validation_errors: list[str] = []
    success_message = ""

    # ---------- store params into session ----------
    if form_submitted:
        target_cct = _require_float("target_cct", positive=True, label="Target CCT", errors=validation_errors)
        target_cri = _require_float("target_cri", positive=True, label="Target CRI", errors=validation_errors)
        target_lumen = _require_float(
            "target_lumen", positive=True, label="Target Luminaire Lumen Output", errors=validation_errors
        )
        target_efficacy = _require_float(
            "target_efficacy", positive=True, label="Target Luminaire Efficacy (lm/W)", errors=validation_errors
        )
        optical_transmission = _require_float(
            "optical_transmission", min_val=1, max_val=100, label="Optical Transmission (%)", errors=validation_errors
        )
        power_efficiency = _require_float(
            "power_efficiency", min_val=1, max_val=100, label="Power Supply Efficiency (%)", errors=validation_errors
        )
        junction_temp = _require_float("junction_temp", label="Junction Temperature (°C)", errors=validation_errors)
        v_chain_max = _require_float(
            "v_chain_max", positive=True, label="Maximum LED Chain Voltage (V)", errors=validation_errors
        )
        smt_cost_rmb = _require_float("smt_cost_rmb", min_val=0, label="SMT Cost (RMB)", errors=validation_errors)
        usd_rate = _require_float("usd_rate", positive=True, label="USD Exchange Rate", errors=validation_errors)

        # store only validated values
        if target_cct is not None:
            session["target_cct"] = target_cct
        if target_cri is not None:
            session["target_cri"] = target_cri
        if target_lumen is not None:
            session["target_lumen"] = target_lumen
        if target_efficacy is not None:
            session["target_efficacy"] = target_efficacy
        if optical_transmission is not None:
            session["optical_transmission"] = optical_transmission
        if power_efficiency is not None:
            session["power_efficiency"] = power_efficiency
        if junction_temp is not None:
            session["junction_temp"] = junction_temp
        if v_chain_max is not None:
            session["v_chain_max"] = v_chain_max
        if smt_cost_rmb is not None:
            session["smt_cost_rmb"] = smt_cost_rmb
        if usd_rate is not None:
            session["usd_rate"] = usd_rate

        if not validation_errors:
            success_message = "Parameters successfully stored! Ready for LED count calculations."

    # ---------- defaults for template ----------
    target_cct = float(session.get("target_cct", 4000))
    target_cri = float(session.get("target_cri", 80))
    target_lumen = float(session.get("target_lumen", 5000))
    target_efficacy = float(session.get("target_efficacy", 125))
    optical_transmission = float(session.get("optical_transmission", 80))
    power_efficiency = float(session.get("power_efficiency", 85))
    junction_temp = float(session.get("junction_temp", 65))
    v_chain_max = float(session.get("v_chain_max", 50))
    smt_cost_rmb = float(session.get("smt_cost_rmb", 0.01))
    usd_rate = float(session.get("usd_rate", 7.00))

    connection_status = "Failed"
    error_message = ""
    cct_options: list[float] = []
    cri_options: list[float] = []

    # ---------- DB status + dropdown options ----------
    try:
        pairs = db.fetch_distinct_cct_cri()
        cct_options = sorted({p[0] for p in pairs if p and p[0] is not None})
        cri_options = sorted({p[1] for p in pairs if p and p[1] is not None})
        connection_status = "Success"
    except Exception as e:
        connection_status = "Failed"
        error_message = str(e)
        traceback.print_exc()

    # ---------- derive targets (numeric-domain, no PHP-empty semantics) ----------
    optical_rate = optical_transmission / 100.0
    power_rate = power_efficiency / 100.0
    combined = optical_rate * power_rate

    target_led_lumen = (target_lumen / optical_rate) if optical_rate > 0 else 0.0
    target_led_efficacy = (target_efficacy / combined) if combined > 0 else 0.0

    # ---------- query + algorithm ----------
    led_candidates = []
    led_config_solutions = {}
    candidate_count = 0

    if form_submitted and (not validation_errors) and target_cct > 0 and target_cri > 0:
        try:
            candidate_rows = db.fetch_candidates_by_cct_cri(target_cct, target_cri)
            led_candidates, led_config_solutions = process_led_candidates(
                candidate_rows=candidate_rows,
                target_led_efficacy=target_led_efficacy,
                target_led_lumen=target_led_lumen,
                junction_temp=junction_temp,
                v_chain_max=v_chain_max,
            )
            candidate_count = len(led_candidates)
        except Exception as e:
            error_message = f"Algorithm/Query error: {e}"
            traceback.print_exc()

    # ---------- build displays (template requires derived fields) ----------
    sorted_candidates_display = []
    candidate_costs_display = []

    if candidate_count > 0:
        # constant precompute
        smt_cost_rmb_f = float(smt_cost_rmb)
        usd_rate_f = float(usd_rate)
        target_efficacy_f = float(target_efficacy)

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
                    total_leds=total_leds, unit_usd=unit_usd, smt_cost_rmb=smt_cost_rmb_f, usd_rate=usd_rate_f
                )
                led_cost_rmb, smt_cost_rmb_total, total_cost_rmb = _cost_rmb(
                    total_leds=total_leds, unit_rmb=unit_rmb, smt_cost_rmb=smt_cost_rmb_f
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

            candidate_costs_display.append({"index": idx, "candidate": cand, "solutions_display": solutions_display})

    return render_template(
        "main.html",
        form_submitted=form_submitted,
        validation_errors=validation_errors,
        success_message=success_message,
        target_cct=target_cct,
        target_lumen=target_lumen,
        target_efficacy=target_efficacy,
        junction_temp=junction_temp,
        v_chain_max=v_chain_max,
        smt_cost_rmb=smt_cost_rmb,
        usd_rate=usd_rate,
        optical_transmission=optical_transmission,
        power_efficiency=power_efficiency,
        target_cri=target_cri,
        connection_status=connection_status,
        error_message=error_message,
        cct_options=cct_options,
        cri_options=cri_options,
        led_config_solutions=led_config_solutions,
        candidate_count=candidate_count,
        target_led_lumen=target_led_lumen,
        target_led_efficacy=target_led_efficacy,
        sorted_candidates_display=sorted_candidates_display,
        candidate_costs_display=candidate_costs_display,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
