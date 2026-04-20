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
    price_distance_at_least,
    price_gap_matches,
    prices_equal,
)

def classify_order_shape(orders):   
    """
    识别当前挂单形态，并在合法双边单时返回 pair_state
    shape: PAIR_MODE | BUY_ONLY_MODE | SELL_ONLY_MODE | ABNORMAL_MODE
    pair_state: {"mode": PAIR_MODE, "buy_price": float, "sell_price": float} or None
    """
    if len(orders) == 2:
        buy_price = next((o["price"] for o in orders if o["side"] == "BUY"), None)
        sell_price = next((o["price"] for o in orders if o["side"] == "SELL"), None)

        if not buy_price or not sell_price or buy_price >= sell_price:
            return ABNORMAL_MODE, None

        expected_gap = (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP
        if not price_gap_matches(buy_price, sell_price, expected_gap):
            return ABNORMAL_MODE, None

        return PAIR_MODE, {
            "mode": PAIR_MODE,
            "buy_price": buy_price,
            "sell_price": sell_price,
        }

    if len(orders) == 1:
        shape = BUY_ONLY_MODE if orders[0]["side"] == "BUY" else SELL_ONLY_MODE
        return shape, None

    return ABNORMAL_MODE, None

def decide_cycle_action(orders, saved_state, current_btc_mid=None):
    """主循环决策逻辑"""
    shape, pair_state = classify_order_shape(orders)
    saved_mode= saved_state["mode"]

    if saved_mode== PAIR_MODE:    # 填充检测：任一侧被成交则触发 rebuild
        if shape == SELL_ONLY_MODE:
            log_msg(f"🔥 BUY filled - {format_price(saved_state['buy_price'])}")
            return "rebuild", saved_state["buy_price"]
        if shape == BUY_ONLY_MODE:
            log_msg(f"✅ SELL filled - {format_price(saved_state['sell_price'])}")
            return "rebuild", saved_state["sell_price"]

        if pair_state and prices_equal(pair_state["buy_price"], saved_state["buy_price"]) and \
           prices_equal(pair_state["sell_price"], saved_state["sell_price"]):
            log_keep_state("pair", "Keep")
            return "keep", None

    elif saved_mode== BUY_ONLY_MODE:
        if not orders: # 残余买单成交
            log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
            return "rebuild", saved_state["buy_price"]
        
        if shape == BUY_ONLY_MODE:
            buy_order = orders[0]
            
            if REANCHOR_BREAK and current_btc_mid and current_btc_mid > 0:  # 重锚点检测 (Anchor Break)
                distance = (BUY_GRID_FACTOR + REANCHOR_BREAK_STEPS) * GRID_STEP     # 已漂移至少 REANCHOR_BREAK_STEPS * GRID_STEP
                if price_distance_at_least(current_btc_mid, buy_order["price"], distance):
                    log_msg("🪝 contract: anchor break")
                    return "rebuild", None
            log_keep_state("buy-only", "keep")
            return "keep", None

    elif saved_mode== SELL_ONLY_MODE:
        if not orders: # 残余卖单成交
            log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
            return "rebuild", saved_state["sell_price"]
        if shape == SELL_ONLY_MODE:
            log_keep_state("sell-only", "keep")
            return "keep", None

    return "abnormal", None
