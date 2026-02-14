from decimal import Decimal, ROUND_HALF_UP

from flask import Flask, render_template, request, session

from algorithm import build_candidate_costs_for_config
from algorithm import build_sorted_candidates_for_search
from algorithm import process_led_candidates
import db

app = Flask(__name__)
app.secret_key = "lle_phase_10_secret_key"


def php_empty(value):
    if value is None:
        return True
    if value is False:
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
        value_str = str(value).strip()
        if value_str == "":
            return False
        float(value_str)
        return True
    except Exception:
        return False


def to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def number_format(value, decimals=0):
    try:
        dec = Decimal(str(value))
        if int(decimals) <= 0:
            quant = Decimal("1")
            rounded = dec.quantize(quant, rounding=ROUND_HALF_UP)
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
    form_submitted = False
    validation_errors = []
    success_message = ""

    if request.method == "POST" and "calculate_params" in request.form:
        form_submitted = True

        if "target_cct" in request.values and request.values.get("target_cct") != "" and php_is_numeric(request.values.get("target_cct")):
            session["target_cct"] = float(request.values.get("target_cct"))
        else:
            validation_errors.append("Please select a Target CCT from the dropdown")

        if "target_lumen" in request.values and php_is_numeric(request.values.get("target_lumen")) and to_float(request.values.get("target_lumen")) > 0:
            session["target_lumen"] = float(request.values.get("target_lumen"))
        else:
            validation_errors.append("Target Luminaire Lumen Output must be a positive number")

        if "target_efficacy" in request.values:
            value = str(request.values.get("target_efficacy")).replace(",", ".")
            if php_is_numeric(value) and float(value) > 0:
                session["target_efficacy"] = float(value)
            else:
                validation_errors.append("Target Luminaire Efficacy must be a positive number (lm/W)")

        if "optical_transmission" in request.values and php_is_numeric(request.values.get("optical_transmission")) and 1 <= to_float(request.values.get("optical_transmission")) <= 100:
            session["optical_transmission"] = float(request.values.get("optical_transmission"))
        else:
            validation_errors.append("Luminaire Optical Transmission Rate must be between 1-100 percent")

        if "power_efficiency" in request.values and php_is_numeric(request.values.get("power_efficiency")) and 1 <= to_float(request.values.get("power_efficiency")) <= 100:
            session["power_efficiency"] = float(request.values.get("power_efficiency"))
        else:
            validation_errors.append("Power Supply Efficiency must be between 1-100 percent")

        if "junction_temp" in request.values and php_is_numeric(request.values.get("junction_temp")):
            session["junction_temp"] = float(request.values.get("junction_temp"))
        else:
            validation_errors.append("Junction Temperature must be a valid number (°C)")

        if "v_chain_max" in request.values and php_is_numeric(request.values.get("v_chain_max")) and to_float(request.values.get("v_chain_max")) > 0:
            session["v_chain_max"] = float(request.values.get("v_chain_max"))
        else:
            validation_errors.append("Maximum LED Chain Voltage must be a positive number (V)")

        if "smt_cost_rmb" in request.values and php_is_numeric(request.values.get("smt_cost_rmb")) and to_float(request.values.get("smt_cost_rmb")) >= 0:
            session["smt_cost_rmb"] = float(request.values.get("smt_cost_rmb"))
        else:
            validation_errors.append("SMT Cost in RMB must be a positive number or zero")

        if "usd_rate" in request.values and php_is_numeric(request.values.get("usd_rate")) and to_float(request.values.get("usd_rate")) > 0:
            session["usd_rate"] = float(request.values.get("usd_rate"))
        else:
            validation_errors.append("USD Exchange Rate must be a positive number greater than 0")

        if "target_cri" in request.values and request.values.get("target_cri") != "" and php_is_numeric(request.values.get("target_cri")):
            session["target_cri"] = float(request.values.get("target_cri"))
        else:
            validation_errors.append("Please select a Target CRI from the dropdown")

        if len(validation_errors) == 0:
            success_message = "Parameters successfully stored! Ready for LED count calculations."

    target_cct = session.get("target_cct", 4000)
    target_lumen = session.get("target_lumen", 5000)
    target_efficacy = session.get("target_efficacy", 125)
    junction_temp = session.get("junction_temp", 65)
    v_chain_max = session.get("v_chain_max", 50)
    smt_cost_rmb = session.get("smt_cost_rmb", 0.01)
    usd_rate = session.get("usd_rate", 7.00)
    optical_transmission = session.get("optical_transmission", 80)
    power_efficiency = session.get("power_efficiency", 85)
    target_cri = session.get("target_cri", 80)

    table_info = []
    connection_status = "Failed"
    error_message = ""
    cct_options = []
    cri_options = []

    try:
        session_db = db.get_connection()
    except Exception as e:
        return f"Connection failed: {e}"

    try:
        rows = db.fetch_distinct_cct_cri()
        for row in rows:
            cct_options.append(row[0])
            cri_options.append(row[1])
        connection_status = "Success"
    except Exception as e:
        error_message = str(e)
        connection_status = "Failed"


    led_candidates = []
    led_config_solutions = {}
    candidate_count = 0
    query_executed = False

    target_led_lumen = 0
    target_led_efficacy = 0

    if form_submitted and len(validation_errors) == 0 and not php_empty(session.get("target_lumen")) and not php_empty(session.get("target_efficacy")) and not php_empty(session.get("optical_transmission")) and not php_empty(session.get("power_efficiency")):
        optical_factor = session.get("optical_transmission") / 100.0
        target_led_lumen = session.get("target_lumen") / optical_factor

        power_factor = session.get("power_efficiency") / 100.0
        combined_efficiency = optical_factor * power_factor
        target_led_efficacy = session.get("target_efficacy") / combined_efficiency

    if form_submitted and len(validation_errors) == 0 and not php_empty(session.get("target_cct")) and not php_empty(session.get("target_cri")):
        query_db = None
        try:
            query_db = db.get_connection()
            candidate_result = db.fetch_candidates_by_cct_cri(
                session.get("target_cct"),
                session.get("target_cri")
            )


            led_candidates, led_config_solutions = process_led_candidates(
                candidate_rows=candidate_result,
                target_led_efficacy=target_led_efficacy,
                target_led_lumen=target_led_lumen,
                junction_temp=session.get("junction_temp"),
                v_chain_max=session.get("v_chain_max"),
            )
            candidate_count = len(led_candidates)
            query_executed = True
        except Exception:
            candidate_count = 0
        finally:
            if query_db is not None:
                query_db.close()

    session_db.close()

    sorted_candidates = []
    candidate_costs = []

    if query_executed:
        sorted_candidates = build_sorted_candidates_for_search(
            led_candidates,
            led_config_solutions,
            smt_cost_rmb,
            usd_rate,
        )

        candidate_costs = build_candidate_costs_for_config(
            led_candidates,
            led_config_solutions,
            smt_cost_rmb,
            usd_rate,
        )

    sorted_candidates_display = []
    for candidate_data in sorted_candidates:
        candidate_index = candidate_data["index"]
        candidate = candidate_data["candidate"]
        first_solution = None

        if candidate_index in led_config_solutions and len(led_config_solutions[candidate_index]) > 0:
            first_solution = led_config_solutions[candidate_index][0]

        fixture_lm = 0
        if first_solution is not None:
            lm_per_led = to_float(candidate.get("lumen_at_target_Tj_target_if", 0), 0)
            led_count_val = first_solution["total_leds"]
            optical_rate = session.get("optical_transmission", 0) / 100.0
            if lm_per_led > 0 and led_count_val > 0 and optical_rate > 0:
                fixture_lm = lm_per_led * led_count_val * optical_rate

        input_power = 0
        if fixture_lm > 0 and to_float(target_efficacy, 0) > 0:
            input_power = fixture_lm / to_float(target_efficacy, 0)

        total_led_count = first_solution["total_leds"] if first_solution is not None else None

        total_cost_usd = None
        led_cost_usd = 0
        smt_cost_usd = 0
        if first_solution is not None:
            led_cost_usd = first_solution["total_leds"] * to_float(candidate.get("USD", 0), 0) if to_float(candidate.get("USD", 0), 0) > 0 else 0
            smt_cost_usd = first_solution["total_leds"] * to_float(smt_cost_rmb, 0) / to_float(usd_rate, 1)
            total_cost_usd = led_cost_usd + smt_cost_usd

        sorted_candidates_display.append({
            "index": candidate_index,
            "candidate": candidate,
            "fixture_lm": fixture_lm,
            "input_power": input_power,
            "total_led_count": total_led_count,
            "total_cost_usd": total_cost_usd,
            "led_cost_usd": led_cost_usd,
            "smt_cost_usd": smt_cost_usd,
        })

    candidate_costs_display = []
    for candidate_data in candidate_costs:
        candidate_index = candidate_data["index"]
        candidate = candidate_data["candidate"]
        solutions_display = []

        for solution in led_config_solutions.get(candidate_index, [])[:10]:
            total_current = to_float(candidate.get("target_if", 0), 0) * solution["P"] if to_float(candidate.get("target_if", 0), 0) > 0 else 0
            voltage = to_float(solution.get("V_chain", 0), 0)
            current_ma = to_float(candidate.get("target_if", 0), 0) * solution["P"] if to_float(candidate.get("target_if", 0), 0) > 0 else 0
            power_watts = (voltage * current_ma / 1000) if (voltage > 0 and current_ma > 0) else 0

            led_cost_usd = solution["total_leds"] * to_float(candidate.get("USD", 0), 0) if to_float(candidate.get("USD", 0), 0) > 0 else 0
            smt_cost_usd = solution["total_leds"] * to_float(smt_cost_rmb, 0) / to_float(usd_rate, 1)
            total_cost_usd = led_cost_usd + smt_cost_usd

            led_cost_rmb = solution["total_leds"] * to_float(candidate.get("RMB", 0), 0) if to_float(candidate.get("RMB", 0), 0) > 0 else 0
            smt_cost_rmb_total = solution["total_leds"] * to_float(smt_cost_rmb, 0)
            total_cost_rmb = led_cost_rmb + smt_cost_rmb_total

            solutions_display.append({
                "solution": solution,
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
            "index": candidate_index,
            "candidate": candidate,
            "solutions_display": solutions_display,
        })

    session_values = {
        "target_cct": session.get("target_cct"),
        "target_cri": session.get("target_cri"),
        "junction_temp": session.get("junction_temp"),
        "v_chain_max": session.get("v_chain_max"),
        "optical_transmission": session.get("optical_transmission"),
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
