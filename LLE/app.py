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


def php_empty(value):
    if value is None or value is False:
        return True
    if value == 0 or value == 0.0:
        return True
    if isinstance(value, str):
        return value == "" or value == "0"
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def php_is_numeric(value):
    try:
        if value is None:
            return False
        if isinstance(value, (int, float, Decimal)):
            return True
        s = str(value).strip()
        if s == "":
            return False
        float(s.replace(",", "."))
        return True
    except Exception:
        return False


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


@app.route("/", methods=["GET", "POST"])
def main():
    form_submitted = request.method == "POST" and "calculate_params" in request.form
    validation_errors = []
    success_message = ""

    # ---------- store params into session ----------
    if form_submitted:
        def require_numeric(field, *, positive=False, min_val=None, max_val=None, label=None):
            v = request.values.get(field, "")
            if not php_is_numeric(v):
                validation_errors.append(f"{label or field} invalid")
                return None
            x = to_float(v)
            if positive and x <= 0:
                validation_errors.append(f"{label or field} must be > 0")
                return None
            if min_val is not None and x < min_val:
                validation_errors.append(f"{label or field} must be >= {min_val}")
                return None
            if max_val is not None and x > max_val:
                validation_errors.append(f"{label or field} must be <= {max_val}")
                return None
            return x

        target_cct = require_numeric("target_cct", positive=True, label="Target CCT")
        target_cri = require_numeric("target_cri", positive=True, label="Target CRI")
        target_lumen = require_numeric("target_lumen", positive=True, label="Target Luminaire Lumen Output")
        target_efficacy = require_numeric("target_efficacy", positive=True, label="Target Luminaire Efficacy (lm/W)")
        optical_transmission = require_numeric("optical_transmission", min_val=1, max_val=100, label="Optical Transmission (%)")
        power_efficiency = require_numeric("power_efficiency", min_val=1, max_val=100, label="Power Supply Efficiency (%)")
        junction_temp = require_numeric("junction_temp", label="Junction Temperature (°C)")
        v_chain_max = require_numeric("v_chain_max", positive=True, label="Maximum LED Chain Voltage (V)")
        smt_cost_rmb = require_numeric("smt_cost_rmb", min_val=0, label="SMT Cost (RMB)")
        usd_rate = require_numeric("usd_rate", positive=True, label="USD Exchange Rate")

        if target_cct is not None: session["target_cct"] = target_cct
        if target_cri is not None: session["target_cri"] = target_cri
        if target_lumen is not None: session["target_lumen"] = target_lumen
        if target_efficacy is not None: session["target_efficacy"] = target_efficacy
        if optical_transmission is not None: session["optical_transmission"] = optical_transmission
        if power_efficiency is not None: session["power_efficiency"] = power_efficiency
        if junction_temp is not None: session["junction_temp"] = junction_temp
        if v_chain_max is not None: session["v_chain_max"] = v_chain_max
        if smt_cost_rmb is not None: session["smt_cost_rmb"] = smt_cost_rmb
        if usd_rate is not None: session["usd_rate"] = usd_rate

        if len(validation_errors) == 0:
            success_message = "Parameters successfully stored! Ready for LED count calculations."

    # ---------- defaults for template ----------
    target_cct = session.get("target_cct", 4000)
    target_cri = session.get("target_cri", 80)
    target_lumen = session.get("target_lumen", 5000)
    target_efficacy = session.get("target_efficacy", 125)
    optical_transmission = session.get("optical_transmission", 80)
    power_efficiency = session.get("power_efficiency", 85)
    junction_temp = session.get("junction_temp", 65)
    v_chain_max = session.get("v_chain_max", 50)
    smt_cost_rmb = session.get("smt_cost_rmb", 0.01)
    usd_rate = session.get("usd_rate", 7.00)

    table_info = []
    connection_status = "Failed"
    error_message = ""
    cct_options = []
    cri_options = []

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

    # ---------- derive targets ----------
    target_led_lumen = 0
    target_led_efficacy = 0
    if not php_empty(optical_transmission) and not php_empty(power_efficiency) and not php_empty(target_lumen) and not php_empty(target_efficacy):
        optical_factor = to_float(optical_transmission, 0) / 100.0
        power_factor = to_float(power_efficiency, 0) / 100.0
        if optical_factor > 0:
            target_led_lumen = to_float(target_lumen, 0) / optical_factor
        combined = optical_factor * power_factor
        if combined > 0:
            target_led_efficacy = to_float(target_efficacy, 0) / combined

    # ---------- query + algorithm ----------
    led_candidates = []
    led_config_solutions = {}
    candidate_count = 0
    query_executed = False

    if form_submitted and len(validation_errors) == 0 and (not php_empty(target_cct)) and (not php_empty(target_cri)):
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
            query_executed = True
        except Exception as e:
            # 关键：别 silent fail，否则你只会看到 500
            error_message = f"Algorithm/Query error: {e}"
            traceback.print_exc()

    # ---------- build displays (the template expects these keys!) ----------
    sorted_candidates = []
    candidate_costs = []
    if query_executed:
        sorted_candidates = build_sorted_candidates_for_search(
            led_candidates, led_config_solutions, smt_cost_rmb, usd_rate
        )
        candidate_costs = build_candidate_costs_for_config(
            led_candidates, led_config_solutions, smt_cost_rmb, usd_rate
        )

    sorted_candidates_display = []
    for item in sorted_candidates:
        idx = item["index"]
        cand = item["candidate"]
        first_solution = None
        if idx in led_config_solutions and led_config_solutions[idx]:
            first_solution = led_config_solutions[idx][0]

        fixture_lm = 0
        if first_solution is not None:
            lm_per_led = to_float(cand.get("lumen_at_target_Tj_target_if", 0), 0)
            led_cnt = to_float(first_solution.get("total_leds", 0), 0)
            optical_rate = to_float(optical_transmission, 0) / 100.0
            if lm_per_led > 0 and led_cnt > 0 and optical_rate > 0:
                fixture_lm = lm_per_led * led_cnt * optical_rate

        input_power = 0
        if fixture_lm > 0 and to_float(target_efficacy, 0) > 0:
            input_power = fixture_lm / to_float(target_efficacy, 0)

        led_cost_usd = 0
        smt_cost_usd = 0
        total_cost_usd = None
        total_led_count = first_solution["total_leds"] if first_solution is not None else None
        if first_solution is not None:
            unit_usd = to_float(cand.get("USD", 0), 0)
            total_leds = to_float(first_solution.get("total_leds", 0), 0)
            if unit_usd > 0:
                led_cost_usd = total_leds * unit_usd
            smt_cost_usd = total_leds * to_float(smt_cost_rmb, 0) / max(to_float(usd_rate, 1), 1e-9)
            total_cost_usd = led_cost_usd + smt_cost_usd

        sorted_candidates_display.append({
            "index": idx,
            "candidate": cand,
            "fixture_lm": fixture_lm,
            "input_power": input_power,
            "total_led_count": total_led_count,
            "total_cost_usd": total_cost_usd,
            "led_cost_usd": led_cost_usd,
            "smt_cost_usd": smt_cost_usd,
        })

    candidate_costs_display = []
    for item in candidate_costs:
        idx = item["index"]
        cand = item["candidate"]
        solutions_display = []

        for sol in led_config_solutions.get(idx, [])[:10]:
            total_current = 0
            if to_float(cand.get("target_if", 0), 0) > 0:
                total_current = to_float(cand.get("target_if", 0), 0) * to_float(sol.get("P", 0), 0)

            voltage = to_float(sol.get("V_chain", 0), 0)
            current_ma = total_current
            power_watts = (voltage * current_ma / 1000.0) if (voltage > 0 and current_ma > 0) else 0

            total_leds = to_float(sol.get("total_leds", 0), 0)
            unit_usd = to_float(cand.get("USD", 0), 0)
            unit_rmb = to_float(cand.get("RMB", 0), 0)

            led_cost_usd = total_leds * unit_usd if unit_usd > 0 else 0
            smt_cost_usd = total_leds * to_float(smt_cost_rmb, 0) / max(to_float(usd_rate, 1), 1e-9)
            total_cost_usd = led_cost_usd + smt_cost_usd

            led_cost_rmb = total_leds * unit_rmb if unit_rmb > 0 else 0
            smt_cost_rmb_total = total_leds * to_float(smt_cost_rmb, 0)
            total_cost_rmb = led_cost_rmb + smt_cost_rmb_total

            solutions_display.append({
                "solution": sol,
                "total_current": total_current,
                "power_watts": power_watts,
                "total_cost_usd": total_cost_usd,
                "total_cost_rmb": total_cost_rmb,
                "led_cost_usd": led_cost_usd,
                "smt_cost_usd": smt_cost_usd,
                "led_cost_rmb": led_cost_rmb,
                "smt_cost_rmb_total": smt_cost_rmb_total,
            })

        candidate_costs_display.append({
            "index": idx,
            "candidate": cand,
            "solutions_display": solutions_display,
        })

    session_values = {
        "target_cct": target_cct,
        "target_cri": target_cri,
        "junction_temp": junction_temp,
        "v_chain_max": v_chain_max,
        "optical_transmission": optical_transmission,
    }

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
        table_info=table_info,
        connection_status=connection_status,
        error_message=error_message,
        cct_options=cct_options,
        cri_options=cri_options,
        led_candidates=led_candidates,
        led_config_solutions=led_config_solutions,
        candidate_count=candidate_count,
        query_executed=query_executed,
        target_led_lumen=target_led_lumen,
        target_led_efficacy=target_led_efficacy,
        sorted_candidates=sorted_candidates,
        sorted_candidates_display=sorted_candidates_display,
        candidate_costs=candidate_costs,
        candidate_costs_display=candidate_costs_display,
        session_values=session_values,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
