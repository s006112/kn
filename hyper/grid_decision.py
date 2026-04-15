"""Decision helpers for the REST polling grid engine."""

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


def get_single_order(orders, side):
    if len(orders) != 1 or orders[0]["side"] != side:
        return None
    return orders[0]


def get_pair_state(orders):
    if len(orders) != 2:
        return None

    buy_price = None
    sell_price = None

    for order in orders:
        if order["side"] == "BUY":
            if buy_price is not None:
                return None
            buy_price = normalize_price(order["price"])
            continue

        if order["side"] == "SELL":
            if sell_price is not None:
                return None
            sell_price = normalize_price(order["price"])
            continue

        return None

    if buy_price is None or sell_price is None:
        return None
    if buy_price <= 0 or not buy_price < sell_price:
        return None

    expected_gap = (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP
    if not price_gap_matches(buy_price, sell_price, expected_gap):
        return None

    pair_center_price = normalize_price((buy_price + sell_price) / 2.0)
    if pair_center_price <= 0:
        return None

    return {
        "mode": PAIR_MODE,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "pair_center_price": pair_center_price,
    }


def get_order_shape(orders):
    if len(orders) == 2:
        return PAIR_MODE if get_pair_state(orders) is not None else ABNORMAL_MODE

    if len(orders) == 1:
        side = orders[0]["side"]
        if side == "BUY":
            return BUY_ONLY_MODE
        if side == "SELL":
            return SELL_ONLY_MODE

    return ABNORMAL_MODE


def get_bootstrap_live_state(orders):
    shape = get_order_shape(orders)

    if shape == PAIR_MODE:
        return get_pair_state(orders)

    if shape == BUY_ONLY_MODE:
        buy_price = normalize_price(orders[0]["price"])
        if buy_price <= 0:
            return None
        return {
            "mode": BUY_ONLY_MODE,
            "buy_price": buy_price,
        }

    if shape == SELL_ONLY_MODE:
        sell_price = normalize_price(orders[0]["price"])
        if sell_price <= 0:
            return None
        return {
            "mode": SELL_ONLY_MODE,
            "sell_price": sell_price,
        }

    return None


def get_loop_action(orders, state, current_btc_mid=None):
    shape = get_order_shape(orders)
    mode = state["mode"]

    if mode == PAIR_MODE:
        if shape == SELL_ONLY_MODE:
            log_msg(f"🔥 BUY filled - {format_price(state['buy_price'])}")
            return "rebuild", state["buy_price"]

        if shape == BUY_ONLY_MODE:
            log_msg(f"✅ SELL filled - {format_price(state['sell_price'])}")
            return "rebuild", state["sell_price"]

        pair_state = get_pair_state(orders)
        if (
            pair_state is not None
            and prices_equal(pair_state["buy_price"], state["buy_price"])
            and prices_equal(pair_state["sell_price"], state["sell_price"])
        ):
            log_keep_state("pair", "Keep State")
            return "keep", None

        return "abnormal", None

    if mode == BUY_ONLY_MODE:
        if not orders:
            log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
            return "rebuild", state["buy_price"]

        buy_order = get_single_order(orders, "BUY")
        if buy_order is None:
            return "abnormal", None

        if REANCHOR_BREAK and current_btc_mid is not None and current_btc_mid > 0:
            distance = REANCHOR_BREAK_STEPS * GRID_STEP
            if price_distance_at_least(current_btc_mid, buy_order["price"], distance):
                log_msg("🪝 contract: anchor break")
                return "rebuild", None

        log_keep_state("buy-only", "Keep State")
        return "keep", None

    if mode == SELL_ONLY_MODE:
        if not orders:
            log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
            return "rebuild", state["sell_price"]

        sell_order = get_single_order(orders, "SELL")
        if sell_order is None:
            return "abnormal", None

        log_keep_state("sell-only", "Keep State")
        return "keep", None

    return "abnormal", None