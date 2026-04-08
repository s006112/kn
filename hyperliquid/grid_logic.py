def count_order_sides(orders):
    buy_count = 0
    sell_count = 0

    for order in orders:
        if order["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    return buy_count, sell_count


def get_pair_state(orders, grid_step, pair_price_tolerance, pair_mode):
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
    if abs((sell_price - buy_price) - (2 * grid_step)) > pair_price_tolerance:
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
    pair_price_tolerance,
    pair_mode,
    buy_only_mode,
    sell_only_mode,
    abnormal_mode,
):
    buy_count, sell_count = count_order_sides(orders)

    if buy_count == 1 and sell_count == 1:
        pair_state = get_pair_state(orders, grid_step, pair_price_tolerance, pair_mode)
        if pair_state is not None:
            return pair_mode
        return abnormal_mode

    if buy_count == 1 and sell_count == 0:
        return buy_only_mode

    if buy_count == 0 and sell_count == 1:
        return sell_only_mode

    return abnormal_mode


def pair_matches_state(current_pair, state, pair_price_tolerance):
    if abs(current_pair["buy_price"] - state["buy_price"]) > pair_price_tolerance:
        return False
    if abs(current_pair["sell_price"] - state["sell_price"]) > pair_price_tolerance:
        return False
    return True


def should_reanchor_residual_order(
    info,
    orders,
    btc_mid_key,
    grid_step,
    reanchor_break,
    reanchor_break_steps,
):
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
        if btc_mid - order_price >= threshold_distance:
            print("re-anchor triggered: stale BUY")
            return True
        return False

    if order_price - btc_mid >= threshold_distance:
        print("re-anchor triggered: stale SELL")
        return True

    return False
