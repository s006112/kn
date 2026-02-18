# solver.py

from algorithm_core import _num

def solve_target_if_newton(
    row,
    k_eta,
    k_phi,
    initial_if,
):
    from algorithm_core import (
        calculateObjectiveFunction,
        calculateObjectiveFunctionDerivative,
    )

    tolerance = 0.0001
    max_iterations = 100
    target_if = float(initial_if)
    iteration_count = 0
    converged = False

    min_abs_f = float("inf")
    hit_bounds_count = 0
    hit_if_max = False
    final_f = None

    if not (k_eta > 0 and k_phi > 0 and row.get("If_max") is not None):
        return target_if, converged, {}

    if_max = _num(row.get("If_max"), 0)
    if if_max <= 0:
        return target_if, converged, {}

    try:
        f_lo = calculateObjectiveFunction(1.0, k_eta, k_phi, row)
    except Exception:
        f_lo = None

    try:
        f_hi = calculateObjectiveFunction(if_max, k_eta, k_phi, row)
    except Exception:
        f_hi = None

    bracket = (f_lo is not None and f_hi is not None and (f_lo * f_hi) <= 0)
    if_max_valid = (f_hi is not None and f_hi <= tolerance)

    try:
        while iteration_count < max_iterations and not converged:
            iteration_count += 1

            f = calculateObjectiveFunction(target_if, k_eta, k_phi, row)
            f_derivative = calculateObjectiveFunctionDerivative(
                target_if, k_eta, k_phi, row
            )

            abs_f = abs(f)
            if abs_f < min_abs_f:
                min_abs_f = abs_f

            if abs_f < tolerance:
                converged = True
                final_f = f
                break

            temp_if = target_if - (f / f_derivative)

            if temp_if < 0 or temp_if > if_max:
                target_if += 5
                hit_bounds_count += 1
            else:
                target_if = temp_if

            if target_if > if_max:
                target_if = if_max
                hit_if_max = True
                break

        if final_f is None:
            final_f = calculateObjectiveFunction(target_if, k_eta, k_phi, row)

    except Exception:
        converged = False

    if not converged and target_if >= if_max and if_max_valid:
        converged = True

    diag = {
        "bracket": bracket,
        "f_lo": f_lo,
        "f_hi": f_hi,
        "if_max_valid": if_max_valid,
        "converged": converged,
        "iter": iteration_count,
        "final_f": final_f,
        "min_abs_f": min_abs_f,
        "hit_bounds": hit_bounds_count,
        "hit_if_max": hit_if_max,
        "target_if": target_if,
    }

    return target_if, converged, diag
