# solver.py
# Pure Newton solver with bounded fallback stepping

from algorithm_core import _num


def solve_target_if_newton(
    row,
    k_eta,
    k_phi,
    initial_if,
    tolerance=0.0001,
    max_iterations=100,
):
    """
    Solve target_if using Newton-Raphson with bounded fallback stepping.

    Returns:
        target_if, converged, iteration_count
    """

    from algorithm_core import (
        calculateObjectiveFunction,
        calculateObjectiveFunctionDerivative,
    )

    target_if = float(initial_if)
    iteration_count = 0
    converged = False

    if not (k_eta > 0 and k_phi > 0 and row.get("If_max") is not None):
        return target_if, converged, iteration_count

    if_max = _num(row.get("If_max"), 0)

    if if_max <= 0:
        return target_if, converged, iteration_count

    try:
        while iteration_count < max_iterations and not converged:
            iteration_count += 1

            f = calculateObjectiveFunction(target_if, k_eta, k_phi, row)
            f_derivative = calculateObjectiveFunctionDerivative(
                target_if, k_eta, k_phi, row
            )

            if abs(f) < tolerance:
                converged = True
                break

            temp_if = target_if - (f / f_derivative)

            # fallback when Newton step exits feasible range
            if temp_if < 0 or temp_if > if_max:
                target_if += 10
            else:
                target_if = temp_if

            if target_if > if_max:
                target_if = if_max
                break

    except Exception:
        converged = False

    return target_if, converged, iteration_count
