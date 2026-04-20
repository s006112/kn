"""Order execution, cleanup, and rebuild helpers for the grid engine."""

import time

from grid_config import (
    ALLOW_BUY_ONLY_WHEN_NO_BTC,
    ALLOW_SELL_ONLY_WHEN_NO_USDC,
    BUDGET_USDC,
    BUY_GRID_FACTOR,
    BUY_ONLY_MODE,
    GRID_STEP,
    PAIR_MODE,
    SELL_GRID_FACTOR,
    SELL_ONLY_MODE,
    SYMBOL,
    WAIT_NO_OPEN_ORDERS_INTERVAL_SEC,
    MAX_RETRIES,
    format_price,
    log_msg,
    summarize_orders,
)
from grid_config import read_orders, read_btc_grid


def build_pair(reference_price):
    if reference_price <= 0:
        raise ValueError("Invalid reference price")

    buy_price = reference_price - (BUY_GRID_FACTOR * GRID_STEP)
    sell_price = reference_price + (SELL_GRID_FACTOR * GRID_STEP)

    if buy_price <= 0:
        raise ValueError("Invalid buy price")

    return (
        {
            "side": "BUY",
            "price": buy_price,
            "size": round(BUDGET_USDC / buy_price, 5),
        },
        {
            "side": "SELL",
            "price": sell_price,
            "size": round(BUDGET_USDC / sell_price, 5),
        },
    )


def classify_order_result(result):
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        log_msg(f"order failed: {result}")
        return "error"

    status = statuses[0]
    if "error" in status:
        error = status["error"]
        log_msg(f"order failed: {error}")
        if "Insufficient spot balance" in error:
            return "insufficient_spot_balance"
        return "error"

    if "resting" in status or "filled" in status:
        return "ok"

    log_msg(f"order failed: {result}")
    return "error"


def place_limit_order(trader, action):
    result = trader.order(
        SYMBOL,
        action["side"] == "BUY",
        float(action["size"]),
        float(action["price"]),
        {"limit": {"tif": "Gtc"}},
        False,
    )
    return classify_order_result(result)


def log_rebuild(reference_price, buy_action, sell_action):
    log_msg(
        f"rebuild | SELL - {format_price(sell_action['price'])} | "
        f"REF - {format_price(reference_price)} | "
        f"BUY - {format_price(buy_action['price'])}"
    )


def make_pair_state(reference_price, buy_action, sell_action):
    return {
        "mode": PAIR_MODE,
        "buy_price": buy_action["price"],
        "sell_price": sell_action["price"],
        "reference_price": reference_price,
    }


def make_buy_only_state(reference_price, buy_action):
    return {
        "mode": BUY_ONLY_MODE,
        "buy_price": buy_action["price"],
        "reference_price": reference_price,
    }


def make_sell_only_state(reference_price, sell_action):
    return {
        "mode": SELL_ONLY_MODE,
        "sell_price": sell_action["price"],
        "reference_price": reference_price,
    }

def wait_until(     # 等待直到满足条件，期间定期检查订单状态，返回是否成功
    info,
    done,
    max_tries=MAX_RETRIES,
    interval_sec=WAIT_NO_OPEN_ORDERS_INTERVAL_SEC,
):
    for _ in range(max_tries):
        if done(read_orders(info)):
            return True
        time.sleep(interval_sec)
    return False

def cancel_order_by_oid(trader, order):
    trader.cancel(SYMBOL, order["oid"])


def cleanup_orders(info, trader, orders):   # 取消所有订单并验证是否成功，返回是否成功
    if not orders:  # 无订单需要清理，直接返回成功
        return True

    for order in orders: # 逐个取消订单
        cancel_order_by_oid(trader, order)

    if wait_until(info, lambda orders: not orders):
        return True

    remaining_orders = read_orders(info)
    summarize_orders(remaining_orders)
    log_msg("cleanup failure: open orders still remain")
    return False


def cleanup_after_partial_place_failure(info, trader):
    remaining_orders = read_orders(info)
    if not remaining_orders:
        return True

    summarize_orders(remaining_orders)
    log_msg("partial placement failure: cleanup remaining orders")

    if cleanup_orders(info, trader, remaining_orders):
        return True

    log_msg("fatal: partial placement cleanup failed")
    return False


def place_pair(info, trader, reference_price):  # 根据参考价构建买卖单，尝试下单，并根据结果返回新的状态
    buy_action, sell_action = build_pair(reference_price)
    log_rebuild(reference_price, buy_action, sell_action)

    buy_status = place_limit_order(trader, buy_action)
    sell_status = place_limit_order(trader, sell_action)

    if buy_status == "ok" and sell_status == "ok":
        return make_pair_state(reference_price, buy_action, sell_action)

    if (
        buy_status == "ok"
        and sell_status == "insufficient_spot_balance"
        and ALLOW_BUY_ONLY_WHEN_NO_BTC
    ):
        log_msg("rebuild -> buy-only")
        return make_buy_only_state(reference_price, buy_action)

    if (
        sell_status == "ok"
        and buy_status == "insufficient_spot_balance"
        and ALLOW_SELL_ONLY_WHEN_NO_USDC
    ):
        log_msg("rebuild -> sell-only")
        return make_sell_only_state(reference_price, sell_action)

    cleanup_after_partial_place_failure(info, trader)
    return None


def is_fill_replace_path(orders, reference_price):  #   判断是否满足填充检测的条件：仅有一个订单，且该订单的价格与参考价的距离符合预期（即该订单很可能是之前挂的单被成交了）
    return (
        len(orders) == 1
        and reference_price is not None
        and orders[0].get("oid") is not None
        and orders[0].get("side") in {"BUY", "SELL"}
    )


def place_fill_replace_pair(info, trader, old_order, reference_price):
    buy_action, sell_action = build_pair(reference_price)
    log_rebuild(reference_price, buy_action, sell_action)

    if old_order["side"] == "SELL":
        first_action = buy_action
        second_action = sell_action
    else:
        first_action = sell_action
        second_action = buy_action

    first_status = place_limit_order(trader, first_action)
    if first_status != "ok":
        return None

    try:
        cancel_order_by_oid(trader, old_order)
        old_order_gone = wait_until(
            info,
            lambda orders: all(order.get("oid") != old_order["oid"] for order in orders),
        )
    except Exception as exc:
        log_msg(f"partial placement failure: cancel/verify exception ({type(exc).__name__}: {exc})")
        cleanup_after_partial_place_failure(info, trader)
        return None

    if not old_order_gone:
        log_msg(f"cleanup failure: old order still remains oid={old_order['oid']}")
        cleanup_after_partial_place_failure(info, trader)
        return None

    second_status = place_limit_order(trader, second_action)
    if second_status == "ok":
        return make_pair_state(reference_price, buy_action, sell_action)

    if (
        second_action["side"] == "SELL"
        and second_status == "insufficient_spot_balance"
        and ALLOW_BUY_ONLY_WHEN_NO_BTC
    ):
        log_msg("rebuild -> buy-only")
        return make_buy_only_state(reference_price, buy_action)

    if (
        second_action["side"] == "BUY"
        and second_status == "insufficient_spot_balance"
        and ALLOW_SELL_ONLY_WHEN_NO_USDC
    ):
        log_msg("rebuild -> sell-only")
        return make_sell_only_state(reference_price, sell_action)

    cleanup_after_partial_place_failure(info, trader)
    return None


def rebuild(info, trader, orders, rebuild_price=None):
    if is_fill_replace_path(orders, rebuild_price):
        return place_fill_replace_pair(info, trader, orders[0], rebuild_price)  # 如果满足填充检测条件，尝试走填充-替换的路径进行重建，这样可以在某些订单被成交的情况下保留部分盈利并快速恢复网格状态

    if orders and not cleanup_orders(info, trader, orders):
        return None

    if rebuild_price is None:
        rebuild_price = read_btc_grid(info)

    return place_pair(info, trader, rebuild_price) # 无论是否有订单需要清理，最终都走一次基于当前参考价的常规重建流程