"""Thin dispatch layer for pair and ladder strategy kernels.

This module keeps the engine-facing action protocol unchanged:
- ("keep", None)
- ("rebuild", reference_price)
- ("rebuild", None)
- ("abnormal", None)

The default runtime path remains the explicit `pair` strategy surface.
In Stage 6, that pair surface may internally reuse ladder `M = 1` on proven
safe paths, but dispatch itself stays decision-only and thin.
"""

from grid_strategy import get_loop_action as get_pair_loop_action
from grid_strategy_ladder import (
    compare_live_ladder_to_expected,
    decide_ladder_action,
    parse_live_ladder_snapshot,
)


def _supports_ladder_state(state):
    if not isinstance(state, dict):
        return False
    if state.get("strategy_kind") != "ladder":
        return False

    reference = state.get("reference")
    parameters = state.get("parameters")
    expected_structure = state.get("expected_structure")

    if not isinstance(reference, dict) or "price" not in reference:
        return False
    if not isinstance(parameters, dict):
        return False
    if not isinstance(expected_structure, dict):
        return False

    required_parameter_keys = (
        "grid_step",
        "buy_grid_factor",
        "sell_grid_factor",
        "depth_per_side",
    )
    required_expected_keys = ("buy_levels", "sell_levels")

    return all(key in parameters for key in required_parameter_keys) and all(
        key in expected_structure for key in required_expected_keys
    )


def _get_ladder_loop_action(orders, state):
    if not _supports_ladder_state(state):
        return "abnormal", None

    live_snapshot = parse_live_ladder_snapshot(orders)
    compare_result = compare_live_ladder_to_expected(state, live_snapshot)
    return decide_ladder_action(compare_result, state)


def get_loop_action(
    info,
    orders,
    state,
    *,
    strategy_kind="pair",
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_mode,
    buy_only_mode,
    sell_only_mode,
    abnormal_mode,
    reanchor_break,
    btc_mid_key,
    reanchor_break_steps,
    log_msg,
    log_keep_state,
    format_price,
):
    """Route the engine loop request to the selected strategy kernel."""
    if strategy_kind == "pair":
        return get_pair_loop_action(
            info,
            orders,
            state,
            grid_step=grid_step,
            buy_grid_factor=buy_grid_factor,
            sell_grid_factor=sell_grid_factor,
            pair_mode=pair_mode,
            buy_only_mode=buy_only_mode,
            sell_only_mode=sell_only_mode,
            abnormal_mode=abnormal_mode,
            reanchor_break=reanchor_break,
            btc_mid_key=btc_mid_key,
            reanchor_break_steps=reanchor_break_steps,
            log_msg=log_msg,
            log_keep_state=log_keep_state,
            format_price=format_price,
        )

    if strategy_kind == "ladder":
        return _get_ladder_loop_action(orders, state)

    return "abnormal", None
