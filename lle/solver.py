# solver.py (REVERT + add feasibility diagnostics)

from algorithm_core import _num

def solve_target_if_newton(
    row,
    k_eta,
    k_phi,
    initial_if,
    tolerance=0.0001,
    max_iterations=100,
):
    from algorithm_core import (
        calculateObjectiveFunction,
        calculateObjectiveFunctionDerivative,
    )

    target_if = float(initial_if)
    iteration_count = 0
    converged = False

    # ---- diagnostic stats ----
    min_abs_f = float("inf")
    hit_bounds_count = 0
    hit_if_max = False
    final_f = None

    if not (k_eta > 0 and k_phi > 0 and row.get("If_max") is not None):
        return target_if, converged, iteration_count

    if_max = _num(row.get("If_max"), 0)
    if if_max <= 0:
        return target_if, converged, iteration_count

    # ---- feasibility / bracketing diagnostics (no behavior change) ----
    try:
        f_lo = calculateObjectiveFunction(1.0, k_eta, k_phi, row)  # 1mA
    except Exception:
        f_lo = None
    try:
        f_hi = calculateObjectiveFunction(if_max, k_eta, k_phi, row)
    except Exception:
        f_hi = None
    bracket = (f_lo is not None and f_hi is not None and (f_lo * f_hi) <= 0)

    try:
        while iteration_count < max_iterations and not converged:
            iteration_count += 1

            f = calculateObjectiveFunction(target_if, k_eta, k_phi, row)
            f_derivative = calculateObjectiveFunctionDerivative(target_if, k_eta, k_phi, row)

            abs_f = abs(f)
            if abs_f < min_abs_f:
                min_abs_f = abs_f

            if abs_f < tolerance:
                converged = True
                final_f = f
                break

            temp_if = target_if - (f / f_derivative)

            # original fallback
            if temp_if < 0 or temp_if > if_max:
                target_if += 10
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

    print(
        "SOLVER_DIAG:",
        row.get("Model"),
        "bracket=", bracket,
        "f@1mA=", (round(f_lo, 6) if f_lo is not None else "NA"),
        "f@if_max=", (round(f_hi, 6) if f_hi is not None else "NA"),
        "converged=", converged,
        "iter=", iteration_count,
        "final_f=", round(final_f, 6),
        "min_abs_f=", round(min_abs_f, 6),
        "hit_bounds=", hit_bounds_count,
        "hit_if_max=", hit_if_max,
        "target_if=", round(target_if, 6),
    )

    return target_if, converged, iteration_count
