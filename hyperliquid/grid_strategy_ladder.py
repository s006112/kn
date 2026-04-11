"""Pure ladder strategy functions plus conservative M=1 compatibility helpers.

This module intentionally stays inside the Stage 4 boundary:
- pure functions only
- no exchange/runtime wiring
- no partial repair or rolling execution
- no strategy selector integration

The first version is intentionally conservative:
- exact level match -> keep
- accepted fill-driven subset -> rebuild with fresh reference
- accepted stale/drift full-ladder shift -> rebuild with fresh reference
- everything else -> abnormal

It does not try to close semantic questions that Stage 3 marked as unresolved,
especially around `depth_per_side = 1` reference rules and residual handling.
"""


def _normalize_price(value):
    return float(value)


def _is_buy_order(order):
    return order["side"] == "B"


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


def _classify_shape(buy_levels, sell_levels):
    if not buy_levels and not sell_levels:
        return "EMPTY"
    if buy_levels and sell_levels:
        return "TWO_SIDED"
    if buy_levels:
        return "BUY_ONLY"
    return "SELL_ONLY"


def _validate_ladder_parameters(
    reference_price,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    depth_per_side,
):
    if depth_per_side < 1:
        raise ValueError("depth_per_side must be >= 1")
    if int(depth_per_side) != depth_per_side:
        raise ValueError("depth_per_side must be an integer")
    if grid_step <= 0:
        raise ValueError("grid_step must be > 0")
    if buy_grid_factor <= 0:
        raise ValueError("buy_grid_factor must be > 0")
    if sell_grid_factor <= 0:
        raise ValueError("sell_grid_factor must be > 0")
    _normalize_price(reference_price)


def _build_levels(reference_price, grid_step, factor, depth_per_side, *, side):
    levels = []
    for level_index in range(1, depth_per_side + 1):
        distance = grid_step * level_index * factor
        if side == "buy":
            levels.append(_normalize_price(reference_price - distance))
        else:
            levels.append(_normalize_price(reference_price + distance))
    return levels


def _extract_expected(saved_ladder_state):
    return {
        "reference_price": saved_ladder_state["reference"]["price"],
        "grid_step": saved_ladder_state["parameters"]["grid_step"],
        "buy_grid_factor": saved_ladder_state["parameters"]["buy_grid_factor"],
        "sell_grid_factor": saved_ladder_state["parameters"]["sell_grid_factor"],
        "depth_per_side": saved_ladder_state["parameters"]["depth_per_side"],
        "buy_levels": list(saved_ladder_state["expected_structure"]["buy_levels"]),
        "sell_levels": list(saved_ladder_state["expected_structure"]["sell_levels"]),
    }


def _is_fill_transition_candidate(live_snapshot, missing_buy_levels, missing_sell_levels, extra_buy_levels, extra_sell_levels):
    if live_snapshot["shape_hint"] != "TWO_SIDED":
        return False
    if extra_buy_levels or extra_sell_levels:
        return False
    return bool(missing_buy_levels or missing_sell_levels)


def _derive_live_reference_price(live_buy_levels, live_sell_levels, grid_step, buy_grid_factor, sell_grid_factor):
    if not live_buy_levels or not live_sell_levels:
        return None

    buy_reference = live_buy_levels[0] + (grid_step * buy_grid_factor)
    sell_reference = live_sell_levels[0] - (grid_step * sell_grid_factor)
    if buy_reference != sell_reference:
        return None
    return _normalize_price(buy_reference)


def _is_shifted_full_ladder_candidate(saved_expected, live_snapshot):
    depth_per_side = saved_expected["depth_per_side"]
    if depth_per_side <= 1:
        return False
    if live_snapshot["shape_hint"] != "TWO_SIDED":
        return False
    if live_snapshot["order_count"]["buy"] != depth_per_side:
        return False
    if live_snapshot["order_count"]["sell"] != depth_per_side:
        return False

    live_reference_price = _derive_live_reference_price(
        live_snapshot["buy_levels"],
        live_snapshot["sell_levels"],
        saved_expected["grid_step"],
        saved_expected["buy_grid_factor"],
        saved_expected["sell_grid_factor"],
    )
    if live_reference_price is None:
        return False

    expected_buy_levels = _build_levels(
        live_reference_price,
        saved_expected["grid_step"],
        saved_expected["buy_grid_factor"],
        depth_per_side,
        side="buy",
    )
    expected_sell_levels = _build_levels(
        live_reference_price,
        saved_expected["grid_step"],
        saved_expected["sell_grid_factor"],
        depth_per_side,
        side="sell",
    )
    return (
        live_snapshot["buy_levels"] == expected_buy_levels
        and live_snapshot["sell_levels"] == expected_sell_levels
    )


def build_expected_ladder_structure(
    reference_price,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    depth_per_side,
):
    """Build the saved expected ladder structure for pure offline evaluation."""
    _validate_ladder_parameters(
        reference_price,
        grid_step,
        buy_grid_factor,
        sell_grid_factor,
        depth_per_side,
    )

    depth_per_side = int(depth_per_side)
    buy_levels = _build_levels(
        reference_price,
        grid_step,
        buy_grid_factor,
        depth_per_side,
        side="buy",
    )
    sell_levels = _build_levels(
        reference_price,
        grid_step,
        sell_grid_factor,
        depth_per_side,
        side="sell",
    )

    return {
        "strategy_kind": "ladder",
        "reference": {
            "kind": "ladder_anchor",
            "price": _normalize_price(reference_price),
        },
        "parameters": {
            "grid_step": _normalize_price(grid_step),
            "buy_grid_factor": _normalize_price(buy_grid_factor),
            "sell_grid_factor": _normalize_price(sell_grid_factor),
            "depth_per_side": depth_per_side,
        },
        "expected_structure": {
            "buy_levels": buy_levels,
            "sell_levels": sell_levels,
        },
    }


def parse_live_ladder_snapshot(current_live_orders):
    """Parse open orders into a pure observed ladder snapshot.

    This function only describes what is currently present.
    It does not decide keep/rebuild/abnormal.
    """
    buy_levels = []
    sell_levels = []

    for order in current_live_orders:
        price = _normalize_price(order["limitPx"])
        if _is_buy_order(order):
            buy_levels.append(price)
        else:
            sell_levels.append(price)

    buy_levels.sort(reverse=True)
    sell_levels.sort()

    return {
        "strategy_kind": "ladder",
        "buy_levels": buy_levels,
        "sell_levels": sell_levels,
        "order_count": _build_order_count(buy_levels, sell_levels),
        "shape_hint": _classify_shape(buy_levels, sell_levels),
    }


def compare_live_ladder_to_expected(saved_ladder_state, live_ladder_snapshot):
    """Compare saved expected ladder levels against the current live snapshot."""
    expected = _extract_expected(saved_ladder_state)
    live_buy_levels = list(live_ladder_snapshot["buy_levels"])
    live_sell_levels = list(live_ladder_snapshot["sell_levels"])

    matched_buy_levels, missing_buy_levels, extra_buy_levels = _diff_levels(
        expected["buy_levels"],
        live_buy_levels,
    )
    matched_sell_levels, missing_sell_levels, extra_sell_levels = _diff_levels(
        expected["sell_levels"],
        live_sell_levels,
    )

    is_exact_keep_candidate = (
        live_ladder_snapshot["shape_hint"] == "TWO_SIDED"
        and not missing_buy_levels
        and not missing_sell_levels
        and not extra_buy_levels
        and not extra_sell_levels
    )
    is_fill_transition_candidate = False
    if not is_exact_keep_candidate:
        is_fill_transition_candidate = _is_fill_transition_candidate(
            live_ladder_snapshot,
            missing_buy_levels,
            missing_sell_levels,
            extra_buy_levels,
            extra_sell_levels,
        )

    is_stale_candidate = False
    if not is_exact_keep_candidate and not is_fill_transition_candidate:
        is_stale_candidate = _is_shifted_full_ladder_candidate(
            expected,
            live_ladder_snapshot,
        )

    is_family_match = (
        is_exact_keep_candidate
        or is_fill_transition_candidate
        or is_stale_candidate
    )
    is_abnormal_candidate = not is_family_match

    return {
        "expected": {
            "buy_levels": list(expected["buy_levels"]),
            "sell_levels": list(expected["sell_levels"]),
        },
        "live": {
            "buy_levels": live_buy_levels,
            "sell_levels": live_sell_levels,
            "shape_hint": live_ladder_snapshot["shape_hint"],
        },
        "matched": {
            "buy_levels": matched_buy_levels,
            "sell_levels": matched_sell_levels,
        },
        "missing": {
            "buy_levels": missing_buy_levels,
            "sell_levels": missing_sell_levels,
        },
        "extra": {
            "buy_levels": extra_buy_levels,
            "sell_levels": extra_sell_levels,
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


def describe_m1_pair_compatibility(
    saved_ladder_state,
    live_ladder_snapshot,
    compare_result=None,
):
    """Describe conservative M=1 live families that a pair wrapper can reuse.

    This helper stays intentionally narrow:
    - it only applies to `depth_per_side = 1`
    - it does not infer rebuild references
    - it does not split BUY_ONLY keep vs stale
    - it does not infer completion side from EMPTY alone
    """
    if compare_result is None:
        compare_result = compare_live_ladder_to_expected(
            saved_ladder_state,
            live_ladder_snapshot,
        )

    expected = _extract_expected(saved_ladder_state)
    if expected["depth_per_side"] != 1:
        return {"is_supported": False, "path": None}

    if compare_result["relation"]["is_exact_keep_candidate"]:
        return {"is_supported": True, "path": "exact_keep"}

    if compare_result["extra"]["buy_levels"] or compare_result["extra"]["sell_levels"]:
        return {"is_supported": False, "path": None}

    expected_buy_levels = list(expected["buy_levels"])
    expected_sell_levels = list(expected["sell_levels"])
    missing_buy_levels = compare_result["missing"]["buy_levels"]
    missing_sell_levels = compare_result["missing"]["sell_levels"]
    live_shape = live_ladder_snapshot["shape_hint"]

    if (
        live_shape == "BUY_ONLY"
        and not missing_buy_levels
        and missing_sell_levels == expected_sell_levels
    ):
        return {"is_supported": True, "path": "buy_only_live"}

    if (
        live_shape == "SELL_ONLY"
        and missing_buy_levels == expected_buy_levels
        and not missing_sell_levels
    ):
        return {"is_supported": True, "path": "sell_only_live"}

    if (
        live_shape == "EMPTY"
        and missing_buy_levels == expected_buy_levels
        and missing_sell_levels == expected_sell_levels
    ):
        return {"is_supported": True, "path": "empty_live"}

    return {"is_supported": False, "path": None}


def decide_ladder_action(compare_result, saved_ladder_state):
    """Return the current engine action tuple for the ladder compare result.

    Stage 4 keeps rebuild reference handling intentionally conservative:
    accepted rebuild paths use `None` so this skeleton does not lock future
    ladder reference rules before Stage 5/6.
    """
    _ = saved_ladder_state
    relation = compare_result["relation"]

    if relation["is_fill_transition_candidate"]:
        return "rebuild", None
    if relation["is_exact_keep_candidate"]:
        return "keep", None
    if relation["is_stale_candidate"]:
        return "rebuild", None
    return "abnormal", None
