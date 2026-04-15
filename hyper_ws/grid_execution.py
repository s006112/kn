"""Order execution, cleanup, and rebuild helpers for the grid engine."""

import time

from grid_gateway import get_mid_reference_price, get_open_orders
from grid_config import (
    ABNORMAL_MODE,
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
    format_price,
    log_msg,
    summarize_orders,
)


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


def place_limit_order(exchange, action):
    result = exchange.order(
        SYMBOL,
        action["side"] == "BUY",
        float(action["size"]),
        float(action["price"]),
        {"limit": {"tif": "Gtc"}},
        False,
    )
    return classify_order_result(result)


def wait_no_open_orders(
    info,
    max_tries=10,
    interval_sec=WAIT_NO_OPEN_ORDERS_INTERVAL_SEC,
):
    for _ in range(max_tries):
        if not get_open_orders(info):
            return True
        time.sleep(interval_sec)
    return False


def cleanup_orders(info, exchange, orders):
    if not orders:
        return True

    exchange.bulk_cancel([{"coin": SYMBOL, "oid": order["oid"]} for order in orders])

    if wait_no_open_orders(info):
        return True

    remaining_orders = get_open_orders(info)
    summarize_orders(remaining_orders)
    log_msg("cleanup failure: open orders still remain")
    return False


def place_pair(exchange, reference_price):
    buy_action, sell_action = build_pair(reference_price)
    log_msg(
        f"rebuild | SELL - {format_price(sell_action['price'])} | "
        f"REF - {format_price(reference_price)} | "
        f"BUY - {format_price(buy_action['price'])}"
    )

    buy_status = place_limit_order(exchange, buy_action)
    sell_status = place_limit_order(exchange, sell_action)

    if buy_status == "ok" and sell_status == "ok":
        return {
            "mode": PAIR_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    if (
        buy_status == "ok"
        and sell_status == "insufficient_spot_balance"
        and ALLOW_BUY_ONLY_WHEN_NO_BTC
    ):
        log_msg("rebuild -> buy-only")
        return {
            "mode": BUY_ONLY_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    if (
        sell_status == "ok"
        and buy_status == "insufficient_spot_balance"
        and ALLOW_SELL_ONLY_WHEN_NO_USDC
    ):
        log_msg("rebuild -> sell-only")
        return {
            "mode": SELL_ONLY_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    return {
        "mode": ABNORMAL_MODE,
        "buy_price": buy_action["price"],
        "sell_price": sell_action["price"],
        "reference_price": reference_price,
    }


def rebuild(info, exchange, orders, reference_price=None):
    if orders and not cleanup_orders(info, exchange, orders):
        return None

    if reference_price is None:
        reference_price = get_mid_reference_price(info)

    new_state = place_pair(exchange, reference_price)
    if new_state["mode"] == ABNORMAL_MODE:
        return None

    return new_state