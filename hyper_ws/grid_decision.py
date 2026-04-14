"""Engine-side decision orchestration helpers for the grid engine."""
from grid_config import (
    BUY_GRID_FACTOR,
    BUY_ONLY_MODE,
    GRID_STEP,
    PAIR_MODE,
    REANCHOR_BREAK,
    REANCHOR_BREAK_STEPS,
    SELL_GRID_FACTOR,
    SELL_ONLY_MODE,
    ABNORMAL_MODE,
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


def should_reanchor_residual_order(
    orders,
    btc_mid,
    grid_step,
    reanchor_break,
    reanchor_break_steps,
):
    if not reanchor_break:
        return False

    if len(orders) != 1:
        return False

    if btc_mid is None or btc_mid <= 0:
        return False

    order = orders[0]
    if order["side"] != "BUY":
        return False

    threshold_distance = reanchor_break_steps * grid_step
    return price_distance_at_least(btc_mid, order["price"], threshold_distance)


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
    """根据当前 live orders 推导启动时可接受的 saved state。

    允许：
    - 合法 PAIR
    - 合法 BUY_ONLY residual
    - 合法 SELL_ONLY residual

    返回:
    - 合法 state dict
    - 或 None（表示当前 live orders 不能直接作为启动 state）
    """
    order_shape = classify_current_order_shape(orders)

    if order_shape == PAIR_MODE:
        return get_current_pair_state(orders)

    if order_shape == BUY_ONLY_MODE:
        if len(orders) != 1 or orders[0]["side"] != "BUY":
            return None

        buy_price = normalize_price(orders[0]["price"])
        reference_price = normalize_price(
            buy_price + (BUY_GRID_FACTOR * GRID_STEP)
        )
        sell_price = normalize_price(
            reference_price + (SELL_GRID_FACTOR * GRID_STEP)
        )

        if buy_price <= 0 or reference_price <= 0 or sell_price <= 0:
            return None

        return {
            "mode": BUY_ONLY_MODE,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "reference_price": reference_price,
        }

    if order_shape == SELL_ONLY_MODE:
        if len(orders) != 1 or orders[0]["side"] != "SELL":
            return None

        sell_price = normalize_price(orders[0]["price"])
        reference_price = normalize_price(
            sell_price - (SELL_GRID_FACTOR * GRID_STEP)
        )
        buy_price = normalize_price(
            reference_price - (BUY_GRID_FACTOR * GRID_STEP)
        )

        if buy_price <= 0 or reference_price <= 0 or sell_price <= 0:
            return None

        return {
            "mode": SELL_ONLY_MODE,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "reference_price": reference_price,
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

    log_keep_state("pair", "contract: keep pair")
    return True


def detect_anchor_break(current_btc_mid, orders, state, order_shape):
    if not REANCHOR_BREAK:
        return None
    if state["mode"] != BUY_ONLY_MODE:
        return None
    if order_shape != BUY_ONLY_MODE:
        return None
    if len(orders) != 1:
        return None

    if not should_reanchor_residual_order(
        orders,
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
        if len(orders) != 1 or orders[0]["side"] != "BUY":
            return None

        anchor_break_action = detect_anchor_break(
            current_btc_mid,
            orders,
            state,
            order_shape,
        )
        if anchor_break_action is not None:
            return anchor_break_action

        log_keep_state("buy-only", "contract: keep buy-only")
        return "keep", None

    if state["mode"] == SELL_ONLY_MODE:
        if order_shape != SELL_ONLY_MODE:
            return None
        if len(orders) != 1 or orders[0]["side"] != "SELL":
            return None

        log_keep_state("sell-only", "contract: keep sell-only")
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