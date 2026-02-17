"""
Own file name algorithm.py

Responsibility
Compute LED electrical and optical derived values, generate feasible series-parallel configurations under voltage constraints, and produce cost-ordered candidate views for downstream rendering.

Used by:
* lle/app.py
* lle/compare_coeff.py

Pipelines:
- rows -> derive -> solve -> evaluate -> configure -> rank -> return

Invariants
- Numeric helper functions always return numeric fallbacks instead of propagating parsing errors.
- Candidate processing always returns a tuple of candidate rows and configuration map.
- Configuration ranking uses deterministic comparator ordering.

Out of scope
- Database access and query construction.
- HTTP request handling and template rendering.
- Currency display formatting for UI output.
"""

import math
from functools import cmp_to_key
from solver import solve_target_if_newton
from topology import generate_config_solutions
from algorithm_core import (
    _num,
    _isset,
    _poly6_value,
    _poly6_derivative,
    calculateFIV,
    calculateFIVDerivative,
    calculateFIL,
    calculateFILDerivative,
    calculateObjectiveFunction,
    calculateObjectiveFunctionDerivative,
    calculateVfWithDebug,
)

def _compare_cost_items(a, b):
    """
    Purpose:
    Compare two candidate cost items by ascending cost.
    Inputs:
    - a: First cost item containing `cost`.
    - b: Second cost item containing `cost`.
    Outputs:
    - int: Comparator result suitable for `cmp_to_key`.
    """
    if a['cost'] == b['cost']:
        return 0
    return -1 if a['cost'] < b['cost'] else 1


def _candidate_cost_item(candidate_index, candidate, led_config_solutions, smt_cost_rmb, usd_rate):
    """
    Purpose:
    Build a normalized cost item from the first configuration solution of a candidate.
    Inputs:
    - candidate_index: Index of candidate in the processed list.
    - candidate: Candidate row with unit pricing fields.
    - led_config_solutions: Mapping from candidate index to configuration solutions.
    - smt_cost_rmb: SMT unit cost in RMB.
    - usd_rate: RMB-to-USD divisor.
    Outputs:
    - dict: Cost item containing index, total cost, and candidate payload.
    """
    first_solution = led_config_solutions[candidate_index][0]
    total_leds = _num(first_solution.get('total_leds', 0), 0)
    unit_usd = _num(candidate.get('USD', 0), 0)
    led_cost_usd = total_leds * unit_usd if unit_usd > 0 else 0
    smt_cost_usd = total_leds * _num(smt_cost_rmb, 0) / max(_num(usd_rate, 1), 1e-9)
    total_cost_usd = led_cost_usd + smt_cost_usd
    return {
        'index': candidate_index,
        'cost': total_cost_usd,
        'candidate': candidate,
    }


def _sorted_candidate_cost_items(led_candidates, led_config_solutions, smt_cost_rmb, usd_rate):
    """
    Purpose:
    Build sorted candidate cost items from available configuration solutions.
    Inputs:
    - led_candidates: Processed candidate rows.
    - led_config_solutions: Mapping from candidate index to configuration solutions.
    - smt_cost_rmb: SMT unit cost in RMB.
    - usd_rate: RMB-to-USD divisor.
    Outputs:
    - list[dict]: Cost items sorted by ascending total cost.
    """
    items = []
    for i, c in enumerate(led_candidates):
        if i in led_config_solutions and led_config_solutions[i]:
            items.append(_candidate_cost_item(i, c, led_config_solutions, smt_cost_rmb, usd_rate))
    return sorted(items, key=cmp_to_key(_compare_cost_items))


def process_led_candidates(candidate_rows, target_led_efficacy, target_led_lumen, junction_temp, v_chain_max):
    """
    Purpose:
    Derive candidate operating points, compute per-candidate optical and electrical fields, and generate feasible series-parallel configurations.
    Inputs:
    - candidate_rows: Source candidate rows from database query results.
    - target_led_efficacy: Target efficacy used for objective scaling.
    - target_led_lumen: Target lumen used to derive required LED count.
    - junction_temp: Junction temperature used for FTL and FTV factor evaluation.
    - v_chain_max: Maximum allowed chain voltage constraint for configuration generation.
    Outputs:
    - tuple[list[dict], dict[int, list[dict]]]: Processed candidate rows and per-candidate configuration solutions.
    """

    led_candidates = []
    led_config_solutions = {}

    for row in candidate_rows:
        row = dict(row)

        tj = _num(junction_temp, 65)
        lm_test_value = _num(row.get('lm_test', 0), 0.0)
        row['lm_test'] = lm_test_value
        lumen_at_25C = 0.0  # FIX: avoid cross-row locals() leakage

        lumen_factor = 0
        try:
            lumen_factor = _poly6_value(tj, row, 'FTL')
            lumen_factor = _num(lumen_factor, 0.0)
        except Exception:
            lumen_factor = 1.0

        vf_factor = 0
        try:
            vf_factor = _poly6_value(tj, row, 'FTV')
            vf_factor = _num(vf_factor, 0.0)
        except Exception:
            vf_factor = 1.0

        k_eta = target_led_efficacy * vf_factor if target_led_efficacy > 0 else 0
        k_phi = lm_test_value * lumen_factor if lm_test_value > 0 else 0

        if _isset(row, 'If') and _num(row['If'], 0) > 0:
            target_if = float(_num(row['If'], 10.0))
        else:
            target_if = 10.0

        initial_if = target_if

        target_if, converged, iteration_count = solve_target_if_newton(
            row=row,
            k_eta=k_eta,
            k_phi=k_phi,
            initial_if=initial_if,
            tolerance=0.0001,
            max_iterations=100,
        )

        lumen_at_target_Tj_target_if = 0
        led_count = 0

        try:
            if lm_test_value > 0:
                fil_at_target_if = calculateFIL(target_if, row)
                lumen_at_25C = lm_test_value * fil_at_target_if
                lumen_at_target_Tj_target_if = lumen_at_25C * lumen_factor

            if target_led_lumen > 0 and lumen_at_target_Tj_target_if > 0:
                led_count = math.ceil(target_led_lumen / lumen_at_target_Tj_target_if)
        except Exception:
            led_count = 0

        # Preserve runtime visibility of solver behavior for each model row.
        print(
            "DEBUG:",
            row.get("Model"),
            "converged=", converged,
            "target_if=", round(target_if, 3),
            "lumen=", round(lumen_at_target_Tj_target_if, 4),
            "led_count=", led_count
        )

        row['led_count'] = led_count
        row['target_if'] = target_if
        row['converged'] = converged

        row['lumen_at_target_Tj_target_if'] = float(lumen_at_target_Tj_target_if)
        # `lumen_at_25C` is conditionally defined when `lm_test_value > 0` in the guarded block above.
        row['lumen_at_25C'] = float(lumen_at_25C)
        row['lumen_factor'] = float(lumen_factor)
        row['vf_factor'] = float(vf_factor)

        try:
            vf_debug = calculateVfWithDebug(target_if, tj, row)
            row['vf_at_target_if'] = float(vf_debug['vf_final'])
        except Exception:
            row['vf_at_target_if'] = 0.0

        row['power_at_target_if'] = float(
            row['vf_at_target_if'] * target_if / 1000.0
        ) if row['vf_at_target_if'] > 0 else 0.0


        led_candidates.append(row)

    for candidate_index, candidate in enumerate(led_candidates):

        required_led_count = candidate.get('led_count', 0)
        vf_single = candidate.get('vf_at_target_if', 0)

        solutions = generate_config_solutions(
            required_led_count=required_led_count,
            vf_single=vf_single,
            v_chain_max=v_chain_max,
        )


        led_config_solutions[candidate_index] = solutions

    return led_candidates, led_config_solutions

def build_sorted_candidates_for_search(led_candidates, led_config_solutions, smt_cost_rmb, usd_rate):
    """
    Purpose:
    Build candidate summary items sorted by total cost for search display.
    Inputs:
    - led_candidates: Processed candidate rows.
    - led_config_solutions: Mapping from candidate index to configuration solutions.
    - smt_cost_rmb: SMT unit cost in RMB.
    - usd_rate: RMB-to-USD divisor.
    Outputs:
    - list[dict]: Candidate summary items sorted by ascending cost.
    """
    if not led_config_solutions:
        return [{'index': i, 'candidate': c} for i, c in enumerate(led_candidates)]
    return _sorted_candidate_cost_items(led_candidates, led_config_solutions, smt_cost_rmb, usd_rate)


def build_candidate_costs_for_config(led_candidates, led_config_solutions, smt_cost_rmb, usd_rate):
    """
    Purpose:
    Build configuration cost items sorted by total cost.
    Inputs:
    - led_candidates: Processed candidate rows.
    - led_config_solutions: Mapping from candidate index to configuration solutions.
    - smt_cost_rmb: SMT unit cost in RMB.
    - usd_rate: RMB-to-USD divisor.
    Outputs:
    - list[dict]: Cost items sorted by ascending total cost.
    """
    if not led_config_solutions:
        return []
    return _sorted_candidate_cost_items(led_candidates, led_config_solutions, smt_cost_rmb, usd_rate)
