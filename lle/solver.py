from algorithm_core import _num, calculateObjectiveFunction, calculateObjectiveFunctionDerivative


def solve_target_if_newton(row, k_eta, k_phi, initial_if, tolerance=0.0001, max_iterations=20):
    if_max = _num(row.get("If_max"), 0)
    if not (k_eta > 0 and k_phi > 0 and if_max > 0):
        return float(initial_if), False, 0

    low, high = 1.0, float(if_max)
    f_low = calculateObjectiveFunction(low, k_eta, k_phi, row)
    f_high = calculateObjectiveFunction(high, k_eta, k_phi, row)

    is_bracketed = f_low * f_high <= 0
    curr_if = float(initial_if)
    converged = False

    for i in range(1, max_iterations + 1):
        f = calculateObjectiveFunction(curr_if, k_eta, k_phi, row)
        df = calculateObjectiveFunctionDerivative(curr_if, k_eta, k_phi, row)

        if abs(f) < tolerance:
            converged = True
            break

        next_if = curr_if - (f / df)

        if is_bracketed and (next_if <= low or next_if >= high):
            next_if = (low + high) / 2.0
        else:
            next_if = max(low, min(high, next_if))

        f_next = calculateObjectiveFunction(next_if, k_eta, k_phi, row)
        if is_bracketed:
            if f_low * f_next <= 0:
                high, f_high = next_if, f_next
            else:
                low, f_low = next_if, f_next

        if abs(next_if - curr_if) < tolerance * 0.1:
            curr_if = next_if
            break

        curr_if = next_if

    return round(curr_if, 6), converged, i
