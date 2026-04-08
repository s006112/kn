"""grid_logic.py

Responsibility
This helper module classifies open-order shapes for the Hyperliquid grid loop, derives validated paired-order state from existing orders, compares paired prices against stored state, and decides when a single residual order has drifted far enough from the BTC mid price to require reanchoring.

Used by:
* hyperliquid/grid_run.py

Pipelines:
- orders -> counts -> pair_state -> shape
- state -> prices -> tolerance -> match
- mode -> mids -> distance -> reanchor

Invariants
- Side `"B"` is always counted as buy; every other side value is counted as sell.
- A paired state is returned only when exactly one buy price and one sell price are present, both prices are positive, the buy price is below the sell price, and the gap matches the configured grid distance within tolerance.
- Residual reanchor checks return `False` when the mode is not single-sided, when reanchoring is disabled, when order count is not one, or when BTC mid lookup fails or is non-positive.

Out of scope
- Placing, canceling, or modifying orders.
- Logging, retries, and loop control.
- Budget sizing, reference-price construction, and exchange credential handling.
"""

def count_order_sides(orders):
    """Purpose:
    Count buy-side and non-buy-side orders.

    Inputs:
    orders: Iterable of order mappings with a `side` field.

    Outputs:
    Returns a `(buy_count, sell_count)` tuple where side `"B"` increments `buy_count` and every other side value increments `sell_count`.
    """
    buy_count = 0
    sell_count = 0

    for order in orders:
        if order["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    return buy_count, sell_count


def get_pair_state(
    orders,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_price_tolerance,
    pair_mode,
):
    """Purpose:
    Validate whether the provided orders form a single grid pair and, if so, build its state snapshot.

    Inputs:
    orders: Iterable of order mappings with `side` and `limitPx` fields.
    grid_step: Price step used to compute the expected gap between the pair prices.
    buy_grid_factor: Multiplier applied to `grid_step` for the buy leg.
    sell_grid_factor: Multiplier applied to `grid_step` for the sell leg.
    pair_price_tolerance: Maximum allowed absolute deviation from the expected gap.
    pair_mode: Mode value stored in the returned state mapping.

    Outputs:
    Returns a mapping with `mode`, `buy_price`, `sell_price`, and `reference_price` when there is exactly one buy order, exactly one non-buy order, both prices are valid, and the observed gap is within tolerance of `(buy_grid_factor + sell_grid_factor) * grid_step`; otherwise returns `None`. Propagates lookup and float-conversion exceptions from malformed order fields.
    """
    buy_price = None
    sell_price = None

    for order in orders:
        if order["side"] == "B":
            if buy_price is not None:
                return None
            buy_price = float(order["limitPx"])
        else:
            if sell_price is not None:
                return None
            sell_price = float(order["limitPx"])

    if buy_price is None or sell_price is None:
        return None

    reference_price = (buy_price + sell_price) / 2.0

    if reference_price <= 0:
        return None
    if buy_price <= 0 or buy_price >= sell_price:
        return None

    expected_gap = (buy_grid_factor + sell_grid_factor) * grid_step
    if abs((sell_price - buy_price) - expected_gap) > pair_price_tolerance:
        return None

    return {
        "mode": pair_mode,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "reference_price": reference_price,
    }


def classify_order_shape(
    orders,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_price_tolerance,
    pair_mode,
    buy_only_mode,
    sell_only_mode,
    abnormal_mode,
):
    """Purpose:
    Classify the current open orders as a valid pair, a single-sided residual, or abnormal.

    Inputs:
    orders: Iterable of order mappings with `side` and, for pair validation, `limitPx` fields.
    grid_step: Price step used when validating a paired order gap.
    buy_grid_factor: Multiplier applied to `grid_step` for the buy leg.
    sell_grid_factor: Multiplier applied to `grid_step` for the sell leg.
    pair_price_tolerance: Maximum allowed absolute deviation from the expected paired gap.
    pair_mode: Return value for a valid paired shape.
    buy_only_mode: Return value for exactly one buy order and no sells.
    sell_only_mode: Return value for exactly one sell order and no buys.
    abnormal_mode: Return value for every other shape, including invalid two-order pairs.

    Outputs:
    Returns one of the provided mode values based on `count_order_sides()` and, for one-buy one-sell cases, `get_pair_state()`. Propagates exceptions raised by pair validation on malformed order fields.
    """
    buy_count, sell_count = count_order_sides(orders)

    if buy_count == 1 and sell_count == 1:
        pair_state = get_pair_state(
            orders,
            grid_step,
            buy_grid_factor,
            sell_grid_factor,
            pair_price_tolerance,
            pair_mode,
        )
        if pair_state is not None:
            return pair_mode
        return abnormal_mode

    if buy_count == 1 and sell_count == 0:
        return buy_only_mode

    if buy_count == 0 and sell_count == 1:
        return sell_only_mode

    return abnormal_mode


def pair_matches_state(current_pair, state, pair_price_tolerance):
    """Purpose:
    Check whether paired buy and sell prices still match a stored state within tolerance.

    Inputs:
    current_pair: Mapping with `buy_price` and `sell_price`.
    state: Mapping with `buy_price` and `sell_price`.
    pair_price_tolerance: Maximum allowed absolute deviation for each side.

    Outputs:
    Returns `True` when both side-price differences are within tolerance; otherwise returns `False`.
    """
    if abs(current_pair["buy_price"] - state["buy_price"]) > pair_price_tolerance:
        return False
    if abs(current_pair["sell_price"] - state["sell_price"]) > pair_price_tolerance:
        return False
    return True


def is_manual_pair_keepable(orders):
    """Purpose:
    Check whether the order set contains exactly one buy order and one sell-side order.

    Inputs:
    orders: Iterable of order mappings with a `side` field.

    Outputs:
    Returns `True` when `count_order_sides()` reports one buy and one sell; otherwise returns `False`.
    """
    buy_count, sell_count = count_order_sides(orders)
    return buy_count == 1 and sell_count == 1


def should_reanchor_residual_order(
    info,
    orders,
    mode,
    buy_only_mode,
    sell_only_mode,
    btc_mid_key,
    grid_step,
    reanchor_break,
    reanchor_break_steps,
):
    """Purpose:
    Decide whether a single residual order should be reanchored after the BTC mid price moves away from it.

    Inputs:
    info: Object exposing `all_mids()` for BTC mid-price lookup.
    orders: Sequence of order mappings expected to contain `side` and `limitPx`.
    mode: Current strategy mode value.
    buy_only_mode: Mode value that represents a buy-side residual.
    sell_only_mode: Mode value that represents a sell-side residual.
    btc_mid_key: Key used to read the BTC mid price from `info.all_mids()`.
    grid_step: Price step multiplied by `reanchor_break_steps` to form the threshold distance.
    reanchor_break: Flag that enables residual reanchor checks.
    reanchor_break_steps: Number of grid steps required before reanchoring.

    Outputs:
    Returns `True` only when reanchoring is enabled, `mode` is single-sided, exactly one order is present, BTC mid lookup succeeds with a positive value, and the mid price has moved away from the residual order by at least `reanchor_break_steps * grid_step`. Returns `False` for disallowed modes, disabled checks, invalid order counts, mid-price lookup failures, and non-positive mid prices. Propagates lookup and float-conversion exceptions from malformed order fields after the mid-price guard passes.
    """
    if mode not in (buy_only_mode, sell_only_mode):
        return False

    if not reanchor_break:
        return False

    if len(orders) != 1:
        return False

    try:
        btc_mid = float(info.all_mids().get(btc_mid_key, 0))
    except Exception:
        return False
    if btc_mid <= 0:
        return False

    threshold_distance = reanchor_break_steps * grid_step
    order = orders[0]
    order_price = float(order["limitPx"])

    if order["side"] == "B":
        return btc_mid - order_price >= threshold_distance

    return order_price - btc_mid >= threshold_distance
