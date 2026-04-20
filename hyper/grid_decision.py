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

def get_pair_state(orders):
    """验证并提取双边网格状态"""
    if len(orders) != 2:
        return None

    buy_price = next((o["price"] for o in orders if o["side"] == "BUY"), None)
    sell_price = next((o["price"] for o in orders if o["side"] == "SELL"), None)

    if not buy_price or not sell_price or buy_price >= sell_price:
        return None

    expected_gap = (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP
    if not price_gap_matches(buy_price, sell_price, expected_gap):
        return None

    return {
        "mode": PAIR_MODE,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "pair_center_price": normalize_price((buy_price + sell_price) / 2.0),
    }

def get_order_shape(orders):
    """识别当前挂单的形态"""
    if len(orders) == 2:
        return PAIR_MODE if get_pair_state(orders) else ABNORMAL_MODE
    if len(orders) == 1:
        return BUY_ONLY_MODE if orders[0]["side"] == "BUY" else SELL_ONLY_MODE
    return ABNORMAL_MODE

def get_loop_action(orders, state, current_btc_mid=None):
    """主循环决策逻辑"""
    shape = get_order_shape(orders)
    mode = state["mode"]

    if mode == PAIR_MODE:
        # 填充检测：任一侧被成交则触发 rebuild
        if shape == SELL_ONLY_MODE:
            log_msg(f"🔥 BUY filled - {format_price(state['buy_price'])}")
            return "rebuild", state["buy_price"]
        if shape == BUY_ONLY_MODE:
            log_msg(f"✅ SELL filled - {format_price(state['sell_price'])}")
            return "rebuild", state["sell_price"]
        
        # 维持状态检测
        pair_state = get_pair_state(orders)
        if pair_state and prices_equal(pair_state["buy_price"], state["buy_price"]) and \
           prices_equal(pair_state["sell_price"], state["sell_price"]):
            log_keep_state("pair", "Keep")
            return "keep", None

    elif mode == BUY_ONLY_MODE:
        if not orders: # 残余买单成交
            log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
            return "rebuild", state["buy_price"]
        
        if shape == BUY_ONLY_MODE:
            buy_order = orders[0]
            # 重锚点检测 (Anchor Break)
            # 语义：当前 BTC mid 相对生成该 residual BUY 的原 reference_price
            # 已漂移至少 REANCHOR_BREAK_STEPS * GRID_STEP
            if REANCHOR_BREAK and current_btc_mid and current_btc_mid > 0:
                distance = (BUY_GRID_FACTOR + REANCHOR_BREAK_STEPS) * GRID_STEP
                if price_distance_at_least(current_btc_mid, buy_order["price"], distance):
                    log_msg("🪝 contract: anchor break")
                    return "rebuild", None
            log_keep_state("buy-only", "keep")
            return "keep", None

    elif mode == SELL_ONLY_MODE:
        if not orders: # 残余卖单成交
            log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
            return "rebuild", state["sell_price"]
        if shape == SELL_ONLY_MODE:
            log_keep_state("sell-only", "keep")
            return "keep", None

    return "abnormal", None