"""Engine-side decision orchestration helpers for the grid engine."""

from grid_config import (
    ABNORMAL_MODE,
    BUY_GRID_FACTOR,
    BUY_ONLY_MODE,
    GRID_STEP,
    PAIR_MODE,
    REANCHOR_BREAK,
    REANCHOR_BREAK_STEPS,
    SELL_GRID_FACTOR,
    SELL_ONLY_MODE,
    format_price,
    log_keep_state,
    log_msg,
    normalize_price,
    price_distance_at_least,
    price_gap_matches,
    prices_equal,
)


def count_order_sides(orders):
    buy_count = 0
    sell_count = 0

    for order in orders:
        if order["side"] == "BUY":
            buy_count += 1
        else:
            sell_count += 1

    return buy_count, sell_count


def get_single_order(orders, side):
    if len(orders) != 1:
        return None
    order = orders[0]
    if order["side"] != side:
        return None
    return order


def get_pair_state(
    orders,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_mode,
):
    buy_price = None
    sell_price = None

    for order in orders:
        if order["side"] == "BUY":
            if buy_price is not None:
                return None
            buy_price = order["price"]
        else:
            if sell_price is not None:
                return None
            sell_price = order["price"]

    if buy_price is None or sell_price is None:
        return None

    pair_center_price = normalize_price((buy_price + sell_price) / 2.0)
    if pair_center_price <= 0:
        return None
    if buy_price <= 0 or not (buy_price < sell_price):
        return None

    expected_gap = (buy_grid_factor + sell_grid_factor) * grid_step
    if not price_gap_matches(buy_price, sell_price, expected_gap):
        return None

    return {
        "mode": pair_mode,
        "buy_price": normalize_price(buy_price),
        "sell_price": normalize_price(sell_price),
        "pair_center_price": pair_center_price,
    }


def classify_order_shape(
    orders,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_mode,
    buy_only_mode,
    sell_only_mode,
    abnormal_mode,
):
    buy_count, sell_count = count_order_sides(orders)

    if buy_count == 1 and sell_count == 1:
        pair_state = get_pair_state(
            orders,
            grid_step,
            buy_grid_factor,
            sell_grid_factor,
            pair_mode,
        )
        return pair_mode if pair_state is not None else abnormal_mode

    if buy_count == 1 and sell_count == 0:
        return buy_only_mode

    if buy_count == 0 and sell_count == 1:
        return sell_only_mode

    return abnormal_mode


def pair_matches_state(current_pair, state):
    return (
        prices_equal(current_pair["buy_price"], state["buy_price"])
        and prices_equal(current_pair["sell_price"], state["sell_price"])
    )


def should_reanchor_buy_order(
    buy_order,
    btc_mid,
    grid_step,
    reanchor_break,
    reanchor_break_steps,
):
    if not reanchor_break:
        return False
    if btc_mid is None or btc_mid <= 0:
        return False

    threshold_distance = reanchor_break_steps * grid_step
    return price_distance_at_least(btc_mid, buy_order["price"], threshold_distance)


def get_current_pair_state(orders):
    return get_pair_state(
        orders,
        GRID_STEP,
        BUY_GRID_FACTOR,
        SELL_GRID_FACTOR,
        PAIR_MODE,
    )


def classify_current_order_shape(orders):
    return classify_order_shape(
        orders,
        GRID_STEP,
        BUY_GRID_FACTOR,
        SELL_GRID_FACTOR,
        PAIR_MODE,
        BUY_ONLY_MODE,
        SELL_ONLY_MODE,
        ABNORMAL_MODE,
    )


def get_bootstrap_live_state(orders):
    order_shape = classify_current_order_shape(orders)

    if order_shape == PAIR_MODE:
        return get_current_pair_state(orders)

    buy_order = get_single_order(orders, "BUY")
    if order_shape == BUY_ONLY_MODE and buy_order is not None:
        buy_price = normalize_price(buy_order["price"])
        if buy_price <= 0:
            return None
        return {
            "mode": BUY_ONLY_MODE,
            "buy_price": buy_price,
        }

    sell_order = get_single_order(orders, "SELL")
    if order_shape == SELL_ONLY_MODE and sell_order is not None:
        sell_price = normalize_price(sell_order["price"])
        if sell_price <= 0:
            return None
        return {
            "mode": SELL_ONLY_MODE,
            "sell_price": sell_price,
        }

    return None


def resolve_fill_rebuild_action(state, orders, order_shape):
    previous_mode = state["mode"]

    if previous_mode == PAIR_MODE:
        if order_shape == SELL_ONLY_MODE:
            log_msg(f"🔥 BUY filled - {format_price(state['buy_price'])}")
            return "rebuild", state["buy_price"]

        if order_shape == BUY_ONLY_MODE:
            log_msg(f"✅ SELL filled - {format_price(state['sell_price'])}")
            return "rebuild", state["sell_price"]

        return None

    if previous_mode == BUY_ONLY_MODE and len(orders) == 0:
        log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
        return "rebuild", state["buy_price"]

    if previous_mode == SELL_ONLY_MODE and len(orders) == 0:
        log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
        return "rebuild", state["sell_price"]

    return None


def validate_keep_state(orders, state, order_shape):
    if state["mode"] != PAIR_MODE:
        return False
    if order_shape != PAIR_MODE:
        return False

    current_pair = get_current_pair_state(orders)
    if current_pair is None:
        return False
    if not pair_matches_state(current_pair, state):
        return False

    log_keep_state("pair", "keep")
    return True


def detect_anchor_break(current_btc_mid, buy_order):
    if buy_order is None:
        return None

    if not should_reanchor_buy_order(
        buy_order,
        current_btc_mid,
        GRID_STEP,
        REANCHOR_BREAK,
        REANCHOR_BREAK_STEPS,
    ):
        return None

    log_msg("🪝 contract: anchor break")
    return "rebuild", None


def detect_single_sided_action(current_btc_mid, orders, state, order_shape):
    if state["mode"] == BUY_ONLY_MODE:
        if order_shape != BUY_ONLY_MODE:
            return None

        buy_order = get_single_order(orders, "BUY")
        if buy_order is None:
            return None

        anchor_break_action = detect_anchor_break(current_btc_mid, buy_order)
        if anchor_break_action is not None:
            return anchor_break_action

        log_keep_state("buy-only", "keep")
        return "keep", None

    if state["mode"] == SELL_ONLY_MODE:
        if order_shape != SELL_ONLY_MODE:
            return None

        sell_order = get_single_order(orders, "SELL")
        if sell_order is None:
            return None

        log_keep_state("sell-only", "keep")
        return "keep", None

    return None


def get_loop_action(orders, state, current_btc_mid=None):
    order_shape = classify_current_order_shape(orders)

    fill_driven_action = resolve_fill_rebuild_action(state, orders, order_shape)
    if fill_driven_action is not None:
        return fill_driven_action

    if validate_keep_state(orders, state, order_shape):
        return "keep", None

    single_sided_action = detect_single_sided_action(
        current_btc_mid,
        orders,
        state,
        order_shape,
    )
    if single_sided_action is not None:
        return single_sided_action

    return "abnormal", None