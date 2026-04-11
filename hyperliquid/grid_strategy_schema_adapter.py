"""grid_strategy_schema_adapter.py

Pure compatibility mappers for expressing the current single-pair runtime
in ladder-first schema language.

This module is intentionally descriptive only:
- no exchange interaction
- no logging
- no side effects
- no runtime decision logic changes

Observed live shape is kept separate from contract-valid pair judgement.
The adapter only classifies what is present in `orders`; it does not mark a
two-sided snapshot as a valid runtime pair until compare-time, when explicit
grid parameters are available.
"""


def _is_buy_order(order):
    return order["side"] == "B"


def _get_order_price(order):
    return float(order["limitPx"])


def _build_order_count(buy_levels, sell_levels):
    return {
        "buy": len(buy_levels),
        "sell": len(sell_levels),
        "total": len(buy_levels) + len(sell_levels),
    }


def _diff_levels(expected_levels, live_levels):
    matched = []
    missing = list(expected_levels)
    extras_pool = list(live_levels)

    for expected_level in expected_levels:
        if expected_level in extras_pool:
            matched.append(expected_level)
            missing.remove(expected_level)
            extras_pool.remove(expected_level)

    return matched, missing, extras_pool


def _is_contract_valid_pair(live_structure, grid_step, buy_grid_factor, sell_grid_factor):
    if live_structure["shape_hint"] != "ONE_BUY_ONE_SELL":
        return False

    buy_levels = live_structure["buy_levels"]
    sell_levels = live_structure["sell_levels"]
    if len(buy_levels) != 1 or len(sell_levels) != 1:
        return False

    buy_price = buy_levels[0]
    sell_price = sell_levels[0]
    if buy_price <= 0 or sell_price <= 0:
        return False
    if buy_price >= sell_price:
        return False

    expected_gap = (buy_grid_factor + sell_grid_factor) * grid_step
    return (sell_price - buy_price) == expected_gap


def classify_live_shape(orders):
    """Classify only the observed live shape, without pair contract judgement."""
    buy_count = 0
    sell_count = 0

    for order in orders:
        if _is_buy_order(order):
            buy_count += 1
        else:
            sell_count += 1

    total = buy_count + sell_count
    if total == 0:
        return "EMPTY"
    if buy_count == 1 and sell_count == 0:
        return "BUY_ONLY"
    if buy_count == 0 and sell_count == 1:
        return "SELL_ONLY"
    if buy_count == 1 and sell_count == 1:
        return "ONE_BUY_ONE_SELL"
    return "ABNORMAL"


def map_pair_state_to_schema_state(
    state,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
):
    """Map current pair saved state into schema state vocabulary."""
    if "pair_center_price" in state:
        reference_kind = "pair_center"
        reference_price = state["pair_center_price"]
    else:
        reference_kind = "placement_anchor"
        reference_price = state.get("reference_price")

    return {
        "strategy_kind": "pair",
        "mode": state["mode"],
        "reference": {
            "kind": reference_kind,
            "price": reference_price,
        },
        "parameters": {
            "grid_step": grid_step,
            "buy_grid_factor": buy_grid_factor,
            "sell_grid_factor": sell_grid_factor,
            "depth_per_side": 1,
        },
        "expected_structure": {
            "buy_levels": [state["buy_price"]],
            "sell_levels": [state["sell_price"]],
        },
    }


def map_orders_to_live_structure(orders):
    """Map current open orders into schema live-structure vocabulary."""
    orders = list(orders)
    buy_levels = []
    sell_levels = []

    for order in orders:
        if _is_buy_order(order):
            buy_levels.append(_get_order_price(order))
        else:
            sell_levels.append(_get_order_price(order))

    return {
        "strategy_kind": "pair",
        "buy_levels": buy_levels,
        "sell_levels": sell_levels,
        "order_count": _build_order_count(buy_levels, sell_levels),
        "shape_hint": classify_live_shape(orders),
    }


def compare_pair_schema_state_to_live_structure(schema_state, live_structure):
    """Compare pair schema state with live structure using explicit parameters."""
    expected_buy_levels = list(schema_state["expected_structure"]["buy_levels"])
    expected_sell_levels = list(schema_state["expected_structure"]["sell_levels"])
    live_buy_levels = list(live_structure["buy_levels"])
    live_sell_levels = list(live_structure["sell_levels"])

    matched_buy_levels, missing_buy_levels, extra_buy_levels = _diff_levels(
        expected_buy_levels,
        live_buy_levels,
    )
    matched_sell_levels, missing_sell_levels, extra_sell_levels = _diff_levels(
        expected_sell_levels,
        live_sell_levels,
    )

    parameters = schema_state["parameters"]
    is_contract_valid_pair = _is_contract_valid_pair(
        live_structure,
        parameters["grid_step"],
        parameters["buy_grid_factor"],
        parameters["sell_grid_factor"],
    )

    mode = schema_state["mode"]
    shape_hint = live_structure["shape_hint"]
    is_exact_keep_candidate = (
        mode == "PAIR"
        and is_contract_valid_pair
        and not missing_buy_levels
        and not missing_sell_levels
        and not extra_buy_levels
        and not extra_sell_levels
    )

    is_fill_transition_candidate = False
    if mode == "PAIR" and shape_hint in ("BUY_ONLY", "SELL_ONLY"):
        is_fill_transition_candidate = True
    elif mode in ("BUY_ONLY", "SELL_ONLY") and shape_hint == "EMPTY":
        is_fill_transition_candidate = True

    is_stale_candidate = mode == "BUY_ONLY" and shape_hint == "BUY_ONLY"

    is_family_match = False
    if is_exact_keep_candidate or is_fill_transition_candidate or is_stale_candidate:
        is_family_match = True
    elif mode == "SELL_ONLY" and shape_hint == "SELL_ONLY":
        is_family_match = True

    is_abnormal_candidate = not is_family_match

    return {
        "expected": {
            "buy_levels": expected_buy_levels,
            "sell_levels": expected_sell_levels,
        },
        "live": {
            "buy_levels": live_buy_levels,
            "sell_levels": live_sell_levels,
            "shape_hint": shape_hint,
            "contract_valid_pair": is_contract_valid_pair,
        },
        "missing": {
            "buy_levels": missing_buy_levels,
            "sell_levels": missing_sell_levels,
        },
        "extra": {
            "buy_levels": extra_buy_levels,
            "sell_levels": extra_sell_levels,
        },
        "matched": {
            "buy_levels": matched_buy_levels,
            "sell_levels": matched_sell_levels,
        },
        "relation": {
            "family_match": is_family_match,
            "is_exact_keep_candidate": is_exact_keep_candidate,
            "is_fill_transition_candidate": is_fill_transition_candidate,
            "is_stale_candidate": is_stale_candidate,
            "is_abnormal_candidate": is_abnormal_candidate,
        },
        "summary": {
            "filled_buy_count": len(missing_buy_levels),
            "filled_sell_count": len(missing_sell_levels),
            "net_shift": len(missing_sell_levels) - len(missing_buy_levels),
        },
    }


def map_action_to_decision_result(action_tuple):
    """Map current tuple action protocol into schema decision vocabulary."""
    action, reference_price = action_tuple

    if action == "keep":
        return {
            "action": "keep",
            "reference": {
                "kind": None,
                "price": None,
            },
            "reason": {
                "category": "keep",
            },
        }

    if action == "rebuild":
        if reference_price is None:
            return {
                "action": "rebuild",
                "reference": {
                    "kind": "placement_anchor",
                    "price": None,
                },
                "reason": {
                    "category": "rebuild_with_fresh_reference",
                },
            }

        return {
            "action": "rebuild",
            "reference": {
                "kind": "placement_anchor",
                "price": reference_price,
            },
            "reason": {
                "category": "rebuild_with_reference",
            },
        }

    if action == "abnormal":
        return {
            "action": "abnormal",
            "reference": {
                "kind": None,
                "price": None,
            },
            "reason": {
                "category": "abnormal",
            },
        }

    raise ValueError(f"Unsupported action: {action!r}")
