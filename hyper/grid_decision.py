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
)

def classify_order_mode(orders):
    """
    识别当前挂单形态，并返回统一的 live state。
    state["mode"]: PAIR_MODE | BUY_ONLY_MODE | SELL_ONLY_MODE | ABNORMAL_MODE
    """
    if len(orders) == 2:
        buy_price = next((o["price"] for o in orders if o["side"] == "BUY"), None)
        sell_price = next((o["price"] for o in orders if o["side"] == "SELL"), None)

        if buy_price is None or sell_price is None or buy_price >= sell_price:
            return {"mode": ABNORMAL_MODE}

        expected_gap = (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP
        if not price_gap_matches(buy_price, sell_price, expected_gap):
            return {"mode": ABNORMAL_MODE}

        return {
            "mode": PAIR_MODE,
            "buy_price": buy_price,
            "sell_price": sell_price,
        }

    if len(orders) == 1:
        order = orders[0]
        if order["side"] == "BUY":
            return {
                "mode": BUY_ONLY_MODE,
                "buy_price": order["price"],
            }

        return {
            "mode": SELL_ONLY_MODE,
            "sell_price": order["price"],
        }

    return {"mode": ABNORMAL_MODE}

def decide_cycle_action(orders, saved_state, current_btc_mid=None):
    """主循环决策逻辑"""
    saved_mode = saved_state["mode"]
    current_state = classify_order_mode(orders)
    current_mode = current_state["mode"]

    if saved_mode == PAIR_MODE:    # 填充检测：任一侧被成交则触发 rebuild
        if current_mode == SELL_ONLY_MODE:
            log_msg(f"🔥 BUY filled - {format_price(saved_state['buy_price'])}")
            return "rebuild", saved_state["buy_price"]
        if current_mode == BUY_ONLY_MODE:
            log_msg(f"✅ SELL filled - {format_price(saved_state['sell_price'])}")
            return "rebuild", saved_state["sell_price"]

        if (
            current_mode == PAIR_MODE
            and current_state["buy_price"] == saved_state["buy_price"]
            and current_state["sell_price"] == saved_state["sell_price"]
        ):
            log_keep_state("pair", "Keep")
            return "keep", None

    elif saved_mode == BUY_ONLY_MODE:
        if not orders: # 残余买单成交
            log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
            return "rebuild", saved_state["buy_price"]
        
        if current_mode == BUY_ONLY_MODE:
            if REANCHOR_BREAK and current_btc_mid and current_btc_mid > 0:  # 重锚点检测 (Anchor Break)
                distance = (BUY_GRID_FACTOR + REANCHOR_BREAK_STEPS) * GRID_STEP     # 已漂移至少 REANCHOR_BREAK_STEPS * GRID_STEP
                if price_distance_at_least(current_btc_mid, current_state["buy_price"], distance):
                    log_msg("🪝 contract: anchor break")
                    return "rebuild", None
            log_keep_state("buy-only", "keep")
            return "keep", None

    elif saved_mode == SELL_ONLY_MODE:
        if not orders: # 残余卖单成交
            log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
            return "rebuild", saved_state["sell_price"]
        if current_mode == SELL_ONLY_MODE:
            log_keep_state("sell-only", "keep")
            return "keep", None

    return "abnormal", None
