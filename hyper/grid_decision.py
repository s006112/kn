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
    Returns:
    - mode: PAIR_MODE | BUY_ONLY_MODE | SELL_ONLY_MODE | ABNORMAL_MODE
    - buy_price: float, 仅在 PAIR_MODE 和 BUY_ONLY_MODE 时有效
    - sell_price: float, 仅在 PAIR_MODE 和 SELL_ONLY_MODE 时有效
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


def decide_cycle_action(live_snapshot, saved_state):
    """
    Returns:
    - action: "keep" | "rebuild" | "abnormal"
    - rebuild_price: float | None, 仅在 action="rebuild" 时有效，表示重建时参考的价格锚点，优先使用成交价，次选当前
    """
    if saved_state["mode"] == PAIR_MODE:    # 填充检测：任一侧被成交则触发 rebuild
        if live_snapshot["mode"] == SELL_ONLY_MODE:
            log_msg(f"🔥 BUY filled - {format_price(saved_state['buy_price'])}")
            return "rebuild", saved_state["buy_price"]
        if live_snapshot["mode"] == BUY_ONLY_MODE:
            log_msg(f"✅ SELL filled - {format_price(saved_state['sell_price'])}")
            return "rebuild", saved_state["sell_price"]

        if (
            live_snapshot["mode"] == PAIR_MODE
            and live_snapshot["buy_price"] == saved_state["buy_price"]
            and live_snapshot["sell_price"] == saved_state["sell_price"]
        ):
            log_keep_state("pair", "Keep")
            return "keep", None

    elif saved_state["mode"] == BUY_ONLY_MODE:
        if not live_snapshot["orders"]:  # 残余买单成交
            log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
            return "rebuild", saved_state["buy_price"]

        if live_snapshot["mode"] == BUY_ONLY_MODE:
            if REANCHOR_BREAK and live_snapshot["btc_mid"] and live_snapshot["btc_mid"] > 0:  # 重锚点检测 (Anchor Break)
                distance = (BUY_GRID_FACTOR + REANCHOR_BREAK_STEPS) * GRID_STEP  # 已漂移至少 REANCHOR_BREAK_STEPS * GRID_STEP
                if price_distance_at_least(live_snapshot["btc_mid"], live_snapshot["buy_price"], distance):
                    log_msg("🪝 contract: anchor break")
                    return "rebuild", None
            log_keep_state("buy-only", "keep")
            return "keep", None

    elif saved_state["mode"] == SELL_ONLY_MODE:
        if not live_snapshot["orders"]:  # 残余卖单成交
            log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
            return "rebuild", saved_state["sell_price"]
        if live_snapshot["mode"] == SELL_ONLY_MODE:
            log_keep_state("sell-only", "keep")
            return "keep", None

    return "abnormal", None
