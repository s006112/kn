# algorithm.py
import math
from solver import solve_target_if_newton
from pricing import sorted_candidate_cost_items
from algorithm_core import (
    _num,
    _poly6_value,
    calculateFIL,
    calculateVfWithDebug,
)


# -------------------------------------------------
# Topology (merged from topology.py)
# -------------------------------------------------

def generate_config_solutions(required_led_count, vf_single, v_chain_max, max_solutions=5):
    solutions = []

    if required_led_count <= 0 or vf_single <= 0:
        return solutions

    v_chain_max_value = _num(v_chain_max, 50)
    S_max = int(v_chain_max_value // vf_single)

    if S_max < 2:
        return solutions

    import math
    P_min = math.ceil(required_led_count / S_max)
    P = P_min

    while len(solutions) < max_solutions:

        S = math.ceil(required_led_count / P)

        if S > S_max:
            P += 1
            continue

        total_leds = P * S
        led_add = total_leds - required_led_count
        V_chain = S * vf_single

        solutions.append({
            "P": P,
            "S": S,
            "led_add": led_add,
            "V_chain": V_chain,
            "total_leds": total_leds,
        })

        P += 1

        if P > required_led_count:
            break

    return solutions


# -------------------------------------------------
# Main LED Processing
# -------------------------------------------------

def process_led_candidates(
    candidate_rows,
    target_led_efficacy,
    target_led_lumen,
    junction_temp,
    v_chain_max,
):
    led_candidates = []
    led_config_solutions = {}

    tj = _num(junction_temp, 65)

    for idx, row in enumerate(candidate_rows):
        row = dict(row)

        lm_test = _num(row.get("lm_test"), 0.0)
        row["lm_test"] = lm_test

        # --- temperature factors ---
        try:
            lumen_factor = _num(_poly6_value(tj, row, "FTL"), 1.0)
        except Exception:
            lumen_factor = 1.0

        try:
            vf_factor = _num(_poly6_value(tj, row, "FTV"), 1.0)
        except Exception:
            vf_factor = 1.0

        # --- objective coefficients ---
        k_eta = target_led_efficacy * vf_factor if target_led_efficacy > 0 else 0.0
        k_phi = lm_test * lumen_factor if lm_test > 0 else 0.0

        # --- initial current ---
        target_if = float(_num(row.get("If"), 10.0))
        initial_if = target_if

        target_if, converged, solver_diag = solve_target_if_newton(
            row=row,
            k_eta=k_eta,
            k_phi=k_phi,
            initial_if=initial_if,
        )

        # --- lumen computation ---
        lumen_at_25C = 0.0
        lumen_at_target = 0.0
        led_count = 0

        if lm_test > 0:
            fil = calculateFIL(target_if, row)
            lumen_at_25C = lm_test * fil
            lumen_at_target = lumen_at_25C * lumen_factor

        if target_led_lumen > 0 and lumen_at_target > 0:
            led_count = math.ceil(target_led_lumen / lumen_at_target)

        # --- voltage ---
        vf_at_target = 0.0
        try:
            vf_debug = calculateVfWithDebug(target_if, tj, row)
            vf_at_target = float(vf_debug["vf_final"])
        except Exception:
            vf_at_target = 0.0

        power = vf_at_target * target_if / 1000.0 if vf_at_target > 0 else 0.0

        # --- DEBUG ---
        print(
            "Model:", row.get("Model"),
            "\n",
            "bracket=", solver_diag.get("bracket"),
            "f@1mA=", (
                round(solver_diag["f_lo"], 2)
                if solver_diag.get("f_lo") is not None
                else "NA"
            ),
            "f@if_max=", (
                round(solver_diag["f_hi"], 2)
                if solver_diag.get("f_hi") is not None
                else "NA"
            ),
            "if_max_valid=", solver_diag.get("if_max_valid"),
            "converged=", converged,
            "iter=", solver_diag.get("iter"),
            "final_f=", round(solver_diag.get("final_f", 0), 2),
            "min_abs_f=", round(solver_diag.get("min_abs_f", 0), 2),
            "hit_bounds=", solver_diag.get("hit_bounds"),
            "hit_if_max=", solver_diag.get("hit_if_max"),
            "target_if=", round(target_if, 2),
            "lumen=", round(lumen_at_target, 2),
            "led_count=", led_count,
            "lumen_factor=", round(lumen_factor, 3),
            "vf_factor=", round(vf_factor, 3),
            "vf_at_target=", round(vf_at_target, 3)
            if vf_at_target > 0
            else 0.0,
            "k_eta=", round(k_eta, 3),
            "k_phi=", round(k_phi, 3),
        )


        row.update(
            {
                "led_count": led_count,
                "target_if": target_if,
                "converged": converged,
                "lumen_at_target_Tj_target_if": float(lumen_at_target),
                "lumen_at_25C": float(lumen_at_25C),
                "lumen_factor": float(lumen_factor),
                "vf_factor": float(vf_factor),
                "vf_at_target_if": float(vf_at_target),
                "power_at_target_if": float(power),
            }
        )

        led_candidates.append(row)

        solutions = generate_config_solutions(
            required_led_count=led_count,
            vf_single=vf_at_target,
            v_chain_max=v_chain_max,
        )

        led_config_solutions[idx] = solutions

    return led_candidates, led_config_solutions


def build_sorted_candidates_for_search(
    led_candidates,
    led_config_solutions,
    smt_cost_rmb,
    usd_rate,
):
    items = sorted_candidate_cost_items(
        led_candidates,
        led_config_solutions,
        smt_cost_rmb,
        usd_rate,
    )

    return [
        item
        for item in items
        if bool(item.get("candidate", {}).get("converged"))
    ]
