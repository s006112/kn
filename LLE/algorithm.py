import math
from functools import cmp_to_key


def _num(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _isset(row, key):
    return key in row and row[key] is not None


def calculateFIV(if_value, row):
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
    try:
        fiv = calculateFIV(if_value, row)
        fil = calculateFIL(if_value, row)
        f = k_eta * (if_value / 1000.0) * fiv - k_phi * fil
        return _num(f, 0.0)
    except Exception:
        return 0.0


def calculateObjectiveFunctionDerivative(if_value, k_eta, k_phi, row):
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
    if a['cost'] == b['cost']:
        return 0
    return -1 if a['cost'] < b['cost'] else 1


def process_led_candidates(candidate_rows, target_led_efficacy, target_led_lumen, junction_temp, v_chain_max):
    led_candidates = []
    led_config_solutions = {}

    for row in candidate_rows:
        row = dict(row)

        tj = _num(junction_temp, 65)

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
            if _isset(row, 'lm_test') and _num(row['lm_test'], 0) > 0:
                k_phi = _num(row['lm_test'], 0) * lumen_factor
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
            if _isset(row, 'lm_test') and _num(row['lm_test'], 0) > 0:
                fil_at_target_if = calculateFIL(target_if, row)
                lumen_at_25C_target_if = _num(row['lm_test'], 0) * fil_at_target_if

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
    sorted_candidates = []

    if led_config_solutions:
        candidate_costs_for_search = []
        for candidate_index, candidate in enumerate(led_candidates):
            if candidate_index in led_config_solutions and led_config_solutions[candidate_index]:
                first_solution = led_config_solutions[candidate_index][0]
                led_cost_usd = first_solution['total_leds'] * _num(candidate.get('USD', 0), 0) if _num(candidate.get('USD', 0), 0) > 0 else 0
                smt_cost_usd = first_solution['total_leds'] * _num(smt_cost_rmb, 0) / _num(usd_rate, 1)
                total_cost_usd = led_cost_usd + smt_cost_usd
                candidate_costs_for_search.append({
                    'index': candidate_index,
                    'cost': total_cost_usd,
                    'candidate': candidate,
                })

        sorted_candidates = sorted(candidate_costs_for_search, key=cmp_to_key(_compare_cost_items))
    else:
        for candidate_index, candidate in enumerate(led_candidates):
            sorted_candidates.append({
                'index': candidate_index,
                'candidate': candidate,
            })

    return sorted_candidates


def build_candidate_costs_for_config(led_candidates, led_config_solutions, smt_cost_rmb, usd_rate):
    candidate_costs = []

    if led_config_solutions:
        for candidate_index, candidate in enumerate(led_candidates):
            if candidate_index in led_config_solutions and led_config_solutions[candidate_index]:
                first_solution = led_config_solutions[candidate_index][0]
                led_cost_usd = first_solution['total_leds'] * _num(candidate.get('USD', 0), 0) if _num(candidate.get('USD', 0), 0) > 0 else 0
                smt_cost_usd = first_solution['total_leds'] * _num(smt_cost_rmb, 0) / _num(usd_rate, 1)
                total_cost_usd = led_cost_usd + smt_cost_usd
                candidate_costs.append({
                    'index': candidate_index,
                    'cost': total_cost_usd,
                    'candidate': candidate,
                })

        candidate_costs = sorted(candidate_costs, key=cmp_to_key(_compare_cost_items))

    return candidate_costs
