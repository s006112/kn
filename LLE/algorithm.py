"""
Own file name algorithm.py

1. Responsibility
Compute LED electrical and optical derived values, generate feasible series-parallel configurations under voltage constraints, and produce cost-ordered candidate views for downstream rendering.

2. Used by
* LLE/app.py

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
        fiv = 0
        fiv += row['FIV_0'] if _isset(row, 'FIV_0') else 0
        fiv += (row['FIV_1'] if _isset(row, 'FIV_1') else 0) * if_value
        fiv += (row['FIV_2'] if _isset(row, 'FIV_2') else 0) * pow(if_value, 2)
        fiv += (row['FIV_3'] if _isset(row, 'FIV_3') else 0) * pow(if_value, 3)
        fiv += (row['FIV_4'] if _isset(row, 'FIV_4') else 0) * pow(if_value, 4)
        fiv += (row['FIV_5'] if _isset(row, 'FIV_5') else 0) * pow(if_value, 5)
        fiv += (row['FIV_6'] if _isset(row, 'FIV_6') else 0) * pow(if_value, 6)
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
        fiv_derivative = 0
        fiv_derivative += row['FIV_1'] if _isset(row, 'FIV_1') else 0
        fiv_derivative += (row['FIV_2'] if _isset(row, 'FIV_2') else 0) * 2 * if_value
        fiv_derivative += (row['FIV_3'] if _isset(row, 'FIV_3') else 0) * 3 * pow(if_value, 2)
        fiv_derivative += (row['FIV_4'] if _isset(row, 'FIV_4') else 0) * 4 * pow(if_value, 3)
        fiv_derivative += (row['FIV_5'] if _isset(row, 'FIV_5') else 0) * 5 * pow(if_value, 4)
        fiv_derivative += (row['FIV_6'] if _isset(row, 'FIV_6') else 0) * 6 * pow(if_value, 5)
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
        fil = 0
        fil += row['FIL_0'] if _isset(row, 'FIL_0') else 0
        fil += (row['FIL_1'] if _isset(row, 'FIL_1') else 0) * if_value
        fil += (row['FIL_2'] if _isset(row, 'FIL_2') else 0) * pow(if_value, 2)
        fil += (row['FIL_3'] if _isset(row, 'FIL_3') else 0) * pow(if_value, 3)
        fil += (row['FIL_4'] if _isset(row, 'FIL_4') else 0) * pow(if_value, 4)
        fil += (row['FIL_5'] if _isset(row, 'FIL_5') else 0) * pow(if_value, 5)
        fil += (row['FIL_6'] if _isset(row, 'FIL_6') else 0) * pow(if_value, 6)
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
        fil_derivative = 0
        fil_derivative += row['FIL_1'] if _isset(row, 'FIL_1') else 0
        fil_derivative += (row['FIL_2'] if _isset(row, 'FIL_2') else 0) * 2 * if_value
        fil_derivative += (row['FIL_3'] if _isset(row, 'FIL_3') else 0) * 3 * pow(if_value, 2)
        fil_derivative += (row['FIL_4'] if _isset(row, 'FIL_4') else 0) * 4 * pow(if_value, 3)
        fil_derivative += (row['FIL_5'] if _isset(row, 'FIL_5') else 0) * 5 * pow(if_value, 4)
        fil_derivative += (row['FIL_6'] if _isset(row, 'FIL_6') else 0) * 6 * pow(if_value, 5)
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


def calculateVf(target_if, target_tj, row):
    """
    Purpose:
    Compute forward voltage at target current and junction temperature.
    Inputs:
    - target_if: Target forward current.
    - target_tj: Target junction temperature.
    - row: Candidate row with FIV and FTV coefficients.
    Outputs:
    - float: Computed forward voltage with fallback defaults on errors.
    """
    try:
        vf_at_25C = calculateFIV(target_if, row)
        vf_factor = 0
        vf_factor += row['FTV_0'] if _isset(row, 'FTV_0') else 0
        vf_factor += (row['FTV_1'] if _isset(row, 'FTV_1') else 0) * target_tj
        vf_factor += (row['FTV_2'] if _isset(row, 'FTV_2') else 0) * pow(target_tj, 2)
        vf_factor += (row['FTV_3'] if _isset(row, 'FTV_3') else 0) * pow(target_tj, 3)
        vf_factor += (row['FTV_4'] if _isset(row, 'FTV_4') else 0) * pow(target_tj, 4)
        vf_factor += (row['FTV_5'] if _isset(row, 'FTV_5') else 0) * pow(target_tj, 5)
        vf_factor += (row['FTV_6'] if _isset(row, 'FTV_6') else 0) * pow(target_tj, 6)
        vf_final = vf_at_25C * _num(vf_factor, 0.0)
        return _num(vf_final, 3.0)
    except Exception:
        return 3.0


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
        vf_factor = 0
        vf_factor += row['FTV_0'] if _isset(row, 'FTV_0') else 0
        vf_factor += (row['FTV_1'] if _isset(row, 'FTV_1') else 0) * target_tj
        vf_factor += (row['FTV_2'] if _isset(row, 'FTV_2') else 0) * pow(target_tj, 2)
        vf_factor += (row['FTV_3'] if _isset(row, 'FTV_3') else 0) * pow(target_tj, 3)
        vf_factor += (row['FTV_4'] if _isset(row, 'FTV_4') else 0) * pow(target_tj, 4)
        vf_factor += (row['FTV_5'] if _isset(row, 'FTV_5') else 0) * pow(target_tj, 5)
        vf_factor += (row['FTV_6'] if _isset(row, 'FTV_6') else 0) * pow(target_tj, 6)
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


def _compare_solutions(a, b):
    """
    Purpose:
    Compare two configuration solutions for deterministic ordering.
    Inputs:
    - a: First solution item containing `S`, `led_add`, and `V_chain`.
    - b: Second solution item containing `S`, `led_add`, and `V_chain`.
    Outputs:
    - int: Comparator result suitable for `cmp_to_key`.
    """
    if a['S'] != b['S']:
        if a['S'] > b['S']:
            return -1
        if a['S'] < b['S']:
            return 1
    if a['led_add'] != b['led_add']:
        if a['led_add'] > b['led_add']:
            return 1
        if a['led_add'] < b['led_add']:
            return -1
    if b['V_chain'] > a['V_chain']:
        return 1
    if b['V_chain'] < a['V_chain']:
        return -1
    return 0


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


def process_led_candidates(candidate_rows, target_led_efficacy, target_led_lumen, junction_temp, v_chain_max):
    """
    Purpose:
    Derive per-candidate operating metrics and generate feasible series-parallel LED configurations.
    Inputs:
    - candidate_rows: Iterable of candidate rows with polynomial coefficients and limits.
    - target_led_efficacy: Required LED-side efficacy target.
    - target_led_lumen: Required LED-side lumen target.
    - junction_temp: Junction temperature used for factor evaluation.
    - v_chain_max: Maximum allowed chain voltage.
    Outputs:
    - tuple[list[dict], dict[int, list[dict]]]: Processed candidates and configuration solutions by index.
    """
    led_candidates = []
    led_config_solutions = {}

    for row in candidate_rows:
        row = dict(row)

        tj = _num(junction_temp, 65)
        lm_test_value = _num(row.get('lm_test', 0), 0.0)
        row['lm_test'] = lm_test_value

        lumen_factor = 0
        try:
            lumen_factor = (
                (row['FTL_0'] if _isset(row, 'FTL_0') else 0) +
                (row['FTL_1'] if _isset(row, 'FTL_1') else 0) * tj +
                (row['FTL_2'] if _isset(row, 'FTL_2') else 0) * pow(tj, 2) +
                (row['FTL_3'] if _isset(row, 'FTL_3') else 0) * pow(tj, 3) +
                (row['FTL_4'] if _isset(row, 'FTL_4') else 0) * pow(tj, 4) +
                (row['FTL_5'] if _isset(row, 'FTL_5') else 0) * pow(tj, 5) +
                (row['FTL_6'] if _isset(row, 'FTL_6') else 0) * pow(tj, 6)
            )
            lumen_factor = _num(lumen_factor, 0.0)
        except Exception:
            lumen_factor = 1.0

        vf_factor = 0
        try:
            vf_factor = (
                (row['FTV_0'] if _isset(row, 'FTV_0') else 0) +
                (row['FTV_1'] if _isset(row, 'FTV_1') else 0) * tj +
                (row['FTV_2'] if _isset(row, 'FTV_2') else 0) * pow(tj, 2) +
                (row['FTV_3'] if _isset(row, 'FTV_3') else 0) * pow(tj, 3) +
                (row['FTV_4'] if _isset(row, 'FTV_4') else 0) * pow(tj, 4) +
                (row['FTV_5'] if _isset(row, 'FTV_5') else 0) * pow(tj, 5) +
                (row['FTV_6'] if _isset(row, 'FTV_6') else 0) * pow(tj, 6)
            )
            vf_factor = _num(vf_factor, 0.0)
        except Exception:
            vf_factor = 1.0

        k_eta = 0
        try:
            if target_led_efficacy > 0:
                k_eta = target_led_efficacy * vf_factor
        except Exception:
            k_eta = 0

        k_phi = 0
        try:
            if lm_test_value > 0:
                k_phi = lm_test_value * lumen_factor
        except Exception:
            k_phi = 0

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
                        target_if = target_if + 10
                    else:
                        target_if = temp_if

                    if target_if > _num(row['If_max'], 0):
                        target_if = _num(row['If_max'], 0)
                        break
            except Exception:
                target_if = 50.0
                converged = False
        else:
            target_if = 50.0
            converged = False

        lumen_at_25C_target_if = 0
        lumen_at_target_Tj_target_if = 0
        led_count = 0

        try:
            if lm_test_value > 0:
                fil_at_target_if = calculateFIL(target_if, row)
                lumen_at_25C_target_if = lm_test_value * fil_at_target_if

            if lumen_at_25C_target_if > 0:
                ftl_at_target_tj = lumen_factor
                lumen_at_target_Tj_target_if = lumen_at_25C_target_if * ftl_at_target_tj

            if target_led_lumen > 0 and lumen_at_target_Tj_target_if > 0:
                led_count = math.ceil(target_led_lumen / lumen_at_target_Tj_target_if)
        except Exception:
            lumen_at_25C_target_if = 0
            lumen_at_target_Tj_target_if = 0
            led_count = 0

        row['calculated_lumen_factor'] = lumen_factor
        row['calculated_vf_factor'] = vf_factor
        row['k_eta'] = k_eta
        row['k_phi'] = k_phi
        row['lumen_at_25C_target_if'] = lumen_at_25C_target_if
        row['lumen_at_target_Tj_target_if'] = lumen_at_target_Tj_target_if
        row['led_count'] = led_count
        row['target_if'] = target_if
        row['iteration_count'] = iteration_count
        row['converged'] = converged

        led_candidates.append(row)

    for candidate_index, candidate in enumerate(led_candidates):
        solutions = []

        required_led_count = candidate['led_count'] if 'led_count' in candidate else 0
        target_if = candidate['target_if'] if 'target_if' in candidate else 0
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
                            'vf_single': vf_single,
                            'vf_at_25C': vf_debug['vf_at_25C'],
                            'fiv': vf_debug['fiv'],
                            'ftv': vf_debug['ftv'],
                            'vf_test': vf_debug['vf_test'],
                        })
                        solution_index += 1

                P += 1

            solutions = sorted(solutions, key=cmp_to_key(_compare_solutions))

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

    items = []
    for i, c in enumerate(led_candidates):
        if i in led_config_solutions and led_config_solutions[i]:
            items.append(_candidate_cost_item(i, c, led_config_solutions, smt_cost_rmb, usd_rate))

    return sorted(items, key=cmp_to_key(_compare_cost_items))


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

    items = []
    for i, c in enumerate(led_candidates):
        if i in led_config_solutions and led_config_solutions[i]:
            items.append(_candidate_cost_item(i, c, led_config_solutions, smt_cost_rmb, usd_rate))

    return sorted(items, key=cmp_to_key(_compare_cost_items))
