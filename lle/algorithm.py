import math
from solver import solve_target_if_newton
from topology import generate_config_solutions
from pricing import sorted_candidate_cost_items
from algorithm_core import (
    _num,
    _poly6_value,
    calculateFIL,
    calculateVfWithDebug,
)


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

        target_if, converged = solve_target_if_newton(
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

        # --- DEBUG visibility ---
        print(
            "DEBUG:",
            row.get("Model"),
            "converged=", converged,
            "target_if=", round(target_if, 3),
            "lumen=", round(lumen_at_target, 4),
            "led_count=", led_count,
        )

        # --- voltage ---
        try:
            vf_debug = calculateVfWithDebug(target_if, tj, row)
            vf_at_target = float(vf_debug["vf_final"])
        except Exception:
            vf_at_target = 0.0

        power = vf_at_target * target_if / 1000.0 if vf_at_target > 0 else 0.0

        # --- write back ---
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

        # --- topology stage moved into same loop ---
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

    # 保持原行为：Search 只显示 converged 的
    return [
        item
        for item in items
        if bool(item.get("candidate", {}).get("converged"))
    ]


def build_candidate_costs_for_config(
    led_candidates,
    led_config_solutions,
    smt_cost_rmb,
    usd_rate,
):
    return sorted_candidate_cost_items(
        led_candidates,
        led_config_solutions,
        smt_cost_rmb,
        usd_rate,
    )
