"""
Own file name algorithm.py

1. Responsibility
Compute LED electrical and optical derived values, generate feasible series-parallel configurations under voltage constraints, and produce cost-ordered candidate views for downstream rendering.

2. Used by
* lle/app.py

3. Pipelines
- rows -> derive -> solve -> filter -> rank -> return

4. Invariants
- Numeric helper functions always return numeric fallbacks instead of propagating parsing errors.
- Candidate processing always returns a tuple of candidate rows and configuration map.
- Configuration ranking uses deterministic comparator ordering.

5. Out of scope
- Database access and query construction.
- HTTP request handling and template rendering.
- Currency display formatting for UI output.
"""

import math
from functools import cmp_to_key


def _num(value, default=0.0):
    """
    Purpose:
    Convert a value to float with a fallback default.
    Inputs:
    - value: Source value to parse as float.
    - default: Fallback value used on parse failure or `None`.
    Outputs:
    - float: Parsed numeric value or fallback.
    """
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _isset(row, key):
    """
    Purpose:
    Check whether a mapping contains a non-`None` value for a key.
    Inputs:
    - row: Mapping-like row object.
    - key: Key to verify.
    Outputs:
    - bool: `True` when key exists and value is not `None`, otherwise `False`.
    """
    return key in row and row[key] is not None


def _poly6_value(x_value, row, prefix):
    """
    Purpose:
    Evaluate a 6th-order polynomial from row coefficients with the given prefix.
    Inputs:
    - x_value: Polynomial input value.
    - row: Candidate row containing `<prefix>_0` through `<prefix>_6`.
    - prefix: Coefficient prefix, e.g. `FIV`, `FIL`, `FTV`, `FTL`.
    Outputs:
    - float: Raw polynomial value.
    """
    value = 0
    value += row[f'{prefix}_0'] if _isset(row, f'{prefix}_0') else 0
    value += (row[f'{prefix}_1'] if _isset(row, f'{prefix}_1') else 0) * x_value
    value += (row[f'{prefix}_2'] if _isset(row, f'{prefix}_2') else 0) * pow(x_value, 2)
    value += (row[f'{prefix}_3'] if _isset(row, f'{prefix}_3') else 0) * pow(x_value, 3)
    value += (row[f'{prefix}_4'] if _isset(row, f'{prefix}_4') else 0) * pow(x_value, 4)
    value += (row[f'{prefix}_5'] if _isset(row, f'{prefix}_5') else 0) * pow(x_value, 5)
    value += (row[f'{prefix}_6'] if _isset(row, f'{prefix}_6') else 0) * pow(x_value, 6)
    return value


def _poly6_derivative(x_value, row, prefix):
    """
    Purpose:
    Evaluate derivative of a 6th-order polynomial from row coefficients with the given prefix.
    Inputs:
    - x_value: Polynomial input value.
    - row: Candidate row containing `<prefix>_1` through `<prefix>_6`.
    - prefix: Coefficient prefix, e.g. `FIV`, `FIL`.
    Outputs:
    - float: Raw derivative value.
    """
    derivative = 0
    derivative += row[f'{prefix}_1'] if _isset(row, f'{prefix}_1') else 0
    derivative += (row[f'{prefix}_2'] if _isset(row, f'{prefix}_2') else 0) * 2 * x_value
    derivative += (row[f'{prefix}_3'] if _isset(row, f'{prefix}_3') else 0) * 3 * pow(x_value, 2)
    derivative += (row[f'{prefix}_4'] if _isset(row, f'{prefix}_4') else 0) * 4 * pow(x_value, 3)
    derivative += (row[f'{prefix}_5'] if _isset(row, f'{prefix}_5') else 0) * 5 * pow(x_value, 4)
    derivative += (row[f'{prefix}_6'] if _isset(row, f'{prefix}_6') else 0) * 6 * pow(x_value, 5)
    return derivative


def calculateFIV(if_value, row):
    """
    Purpose:
    Evaluate the FIV polynomial at the specified current value.
    Inputs:
    - if_value: Forward current value used as polynomial input.
    - row: Candidate row containing `FIV_0` through `FIV_6` coefficients.
    Outputs:
    - float: Evaluated FIV value, with fallback defaults on errors.
    """
    try:
        fiv = _poly6_value(if_value, row, 'FIV')
        return _num(fiv, 1.0)
    except Exception:
        return 1.0


def calculateFIVDerivative(if_value, row):
    """
    Purpose:
    Evaluate the derivative of the FIV polynomial at the specified current value.
    Inputs:
    - if_value: Forward current value used as polynomial input.
    - row: Candidate row containing `FIV_1` through `FIV_6` coefficients.
    Outputs:
    - float: Evaluated derivative value, with fallback defaults on errors.
    """
    try:
        fiv_derivative = _poly6_derivative(if_value, row, 'FIV')
        return _num(fiv_derivative, 0.0)
    except Exception:
        return 0.0


def calculateFIL(if_value, row):
    """
    Purpose:
    Evaluate the FIL polynomial at the specified current value.
    Inputs:
    - if_value: Forward current value used as polynomial input.
    - row: Candidate row containing `FIL_0` through `FIL_6` coefficients.
    Outputs:
    - float: Evaluated FIL value, returning nonzero fallback behavior on errors.
    """
    try:
        fil = _poly6_value(if_value, row, 'FIL')
        fil = _num(fil, 0.0)
        if fil == 0:
            return 1.0
        return fil
    except Exception:
        return 1.0


def calculateFILDerivative(if_value, row):
    """
    Purpose:
    Evaluate the derivative of the FIL polynomial at the specified current value.
    Inputs:
    - if_value: Forward current value used as polynomial input.
    - row: Candidate row containing `FIL_1` through `FIL_6` coefficients.
    Outputs:
    - float: Evaluated derivative value, with fallback defaults on errors.
    """
    try:
        fil_derivative = _poly6_derivative(if_value, row, 'FIL')
        return _num(fil_derivative, 0.0)
    except Exception:
        return 0.0


def calculateObjectiveFunction(if_value, k_eta, k_phi, row):
    """
    Purpose:
    Compute the scalar objective value used by the current solver.
    Inputs:
    - if_value: Current operating point candidate.
    - k_eta: Voltage-related scale factor.
    - k_phi: Lumen-related scale factor.
    - row: Candidate row with FIV and FIL coefficients.
    Outputs:
    - float: Objective value for the provided operating point.
    """
    try:
        fiv = calculateFIV(if_value, row)
        fil = calculateFIL(if_value, row)
        f = k_eta * (if_value / 1000.0) * fiv - k_phi * fil
        return _num(f, 0.0)
    except Exception:
        return 0.0


def calculateObjectiveFunctionDerivative(if_value, k_eta, k_phi, row):
    """
    Purpose:
    Compute the derivative of the scalar objective for Newton updates.
    Inputs:
    - if_value: Current operating point candidate.
    - k_eta: Voltage-related scale factor.
    - k_phi: Lumen-related scale factor.
    - row: Candidate row with FIV and FIL coefficients.
    Outputs:
    - float: Objective derivative with near-zero guard fallback.
    """
    try:
        fiv = calculateFIV(if_value, row)
        fiv_derivative = calculateFIVDerivative(if_value, row)
        fil_derivative = calculateFILDerivative(if_value, row)
        f_derivative = k_eta * (fiv / 1000.0 + (if_value / 1000.0) * fiv_derivative) - k_phi * fil_derivative
        if abs(f_derivative) < 1e-10:
            return 1e-10
        return _num(f_derivative, 1e-10)
    except Exception:
        return 1e-10


def calculateVfWithDebug(target_if, target_tj, row):
    """
    Purpose:
    Compute forward voltage with intermediate fields for diagnostics.
    Inputs:
    - target_if: Target forward current.
    - target_tj: Target junction temperature.
    - row: Candidate row with FIV and FTV coefficients.
    Outputs:
    - dict: Forward voltage and intermediate values used by configuration generation.
    """
    try:
        vf_at_25C = calculateFIV(target_if, row)
        vf_factor = _poly6_value(target_tj, row, 'FTV')
        vf_factor = _num(vf_factor, 0.0)
        vf_final = vf_at_25C * vf_factor
        return {
            'vf_final': _num(vf_final, 3.0),
            'vf_at_25C': _num(vf_at_25C, 3.0),
            'fiv': _num(vf_at_25C, 3.0),
            'ftv': _num(vf_factor, 1.0),
            'vf_test': 'N/A'
        }
    except Exception:
        return {
            'vf_final': 3.0,
            'vf_at_25C': 3.0,
            'fiv': 3.0,
            'ftv': 1.0,
            'vf_test': 'N/A'
        }


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
    Debug-enabled version.
    """

    led_candidates = []
    led_config_solutions = {}

    for row in candidate_rows:
        row = dict(row)

        tj = _num(junction_temp, 65)
        lm_test_value = _num(row.get('lm_test', 0), 0.0)
        row['lm_test'] = lm_test_value

        # ---------------------------
        # Compute lumen factor (FTL)
        # ---------------------------
        lumen_factor = 0
        try:
            lumen_factor = _poly6_value(tj, row, 'FTL')
            lumen_factor = _num(lumen_factor, 0.0)
        except Exception:
            lumen_factor = 1.0

        # ---------------------------
        # Compute vf factor (FTV)
        # ---------------------------
        vf_factor = 0
        try:
            vf_factor = _poly6_value(tj, row, 'FTV')
            vf_factor = _num(vf_factor, 0.0)
        except Exception:
            vf_factor = 1.0

        # ---------------------------
        # Scaling factors
        # ---------------------------
        k_eta = target_led_efficacy * vf_factor if target_led_efficacy > 0 else 0
        k_phi = lm_test_value * lumen_factor if lm_test_value > 0 else 0

        if _isset(row, 'If') and _num(row['If'], 0) > 0:
            target_if = float(_num(row['If'], 10.0))
        else:
            target_if = 10.0
        tolerance = 0.0001
        max_iterations = 100
        iteration_count = 0
        converged = False

        if k_eta > 0 and k_phi > 0 and _isset(row, 'If_max') and _num(row['If_max'], 0) > 0:
            try:
                while iteration_count < max_iterations and not converged:
                    iteration_count += 1
                    f = calculateObjectiveFunction(target_if, k_eta, k_phi, row)
                    f_derivative = calculateObjectiveFunctionDerivative(target_if, k_eta, k_phi, row)

                    if abs(f) < tolerance:
                        converged = True
                        break

                    temp_if = target_if - (f / f_derivative)

                    if temp_if < 0 or temp_if > _num(row['If_max'], 0):
                        target_if += 10
                    else:
                        target_if = temp_if

                    if target_if > _num(row['If_max'], 0):
                        target_if = _num(row['If_max'], 0)
                        break
            except Exception:
                converged = False

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

        # ---------------------------
        # 🔍 DEBUG BLOCK
        # ---------------------------
        print(
            "DEBUG:",
            row.get("Model"),
            "converged=", converged,
            "target_if=", round(target_if, 3),
            "lumen=", round(lumen_at_target_Tj_target_if, 4),
            "led_count=", led_count
        )
        # ---------------------------

        row['led_count'] = led_count
        row['target_if'] = target_if
        row['converged'] = converged

        # ---- MINIMAL FIX: write back computed electrical fields ----
        row['lumen_at_target_Tj_target_if'] = float(lumen_at_target_Tj_target_if)
        row['lumen_at_25C'] = float(lumen_at_25C) if 'lumen_at_25C' in locals() else 0.0
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
        # -------------------------------------------------------------


        led_candidates.append(row)

    # (后半段保持原逻辑不变)
    for candidate_index, candidate in enumerate(led_candidates):
        solutions = []
        required_led_count = candidate.get('led_count', 0)
        target_if = candidate.get('target_if', 0)
        target_tj = _num(junction_temp, 65)
        v_chain_max_value = _num(v_chain_max, 50)

        if required_led_count > 0 and target_if > 0:
            vf_debug = calculateVfWithDebug(target_if, target_tj, candidate)
            vf_single = vf_debug['vf_final']

            P = 1
            solution_index = 0
            max_parallel = min(20, required_led_count)

            while P <= max_parallel and solution_index < 10:
                led_count_working = required_led_count
                led_add = 0

                while (led_count_working % P) != 0:
                    led_count_working += 1
                    led_add += 1

                S = led_count_working / P

                if S >= 2:
                    V_chain = S * vf_single

                    if V_chain <= v_chain_max_value:
                        solutions.append({
                            'P': P,
                            'S': S,
                            'led_add': led_add,
                            'V_chain': V_chain,
                            'total_leds': led_count_working,
                        })
                        solution_index += 1

                P += 1

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
