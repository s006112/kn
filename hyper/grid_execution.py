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
)
from grid_config import read_orders, read_btc_grid


def build_pair(price):
    if price <= 0:
        raise ValueError("Invalid price")

    buy_price = price - (BUY_GRID_FACTOR * GRID_STEP)
    sell_price = price + (SELL_GRID_FACTOR * GRID_STEP)

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


def wait_until(info, done, max_tries=MAX_RETRIES, interval_sec=WAIT_NO_OPEN_ORDERS_INTERVAL_SEC):
    for _ in range(max_tries):
        if done(read_orders(info)):
            return True
        time.sleep(interval_sec)
    return False


def cleanup_orders(info, trader, orders=None):
    
    orders = read_orders(info) if orders is None else orders
    trader.bulk_cancel([{"coin": SYMBOL, "oid": order["oid"]} for order in orders])

    if wait_until(info, lambda orders: not orders):
        return True

    log_msg("cleanup failure: open orders still remain")
    return False


def resolve_rebuild_result(info, trader, reference_price, buy_action, sell_action, second_action, second_status):
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

    log_msg("partial placement failure: cleanup remaining orders")
    cleanup_orders(info, trader)
    return None


def place_reset_rebuild(info, trader, price):
    buy_action, sell_action = build_pair(price)
    log_rebuild(price, buy_action, sell_action)

    buy_status = place_limit_order(trader, buy_action)
    sell_status = place_limit_order(trader, sell_action)

    if buy_status != "ok":
        return resolve_rebuild_result(
            info, trader, price, buy_action, sell_action, buy_action, buy_status
        )

    return resolve_rebuild_result(
        info, trader, price, buy_action, sell_action, sell_action, sell_status
    )


def place_done_deal_rebuild(info, trader, remaining_order, reference_price):
    buy_action, sell_action = build_pair(reference_price)
    log_rebuild(reference_price, buy_action, sell_action)

    if remaining_order["side"] == "SELL":
        first_action = buy_action
        second_action = sell_action
    else:
        first_action = sell_action
        second_action = buy_action

    first_status = place_limit_order(trader, first_action)
    if first_status != "ok":
        return None

    try:
        trader.cancel(SYMBOL, remaining_order["oid"])
        remaining_order_gone = wait_until(
            info,
            lambda orders: all(order.get("oid") != remaining_order["oid"] for order in orders),
        )
    except Exception as exc:
        log_msg(f"partial placement failure: cancel/verify exception ({type(exc).__name__}: {exc})")
        cleanup_orders(info, trader)
        return None

    if not remaining_order_gone:
        log_msg(f"cleanup failure: remaining order still remains oid={remaining_order['oid']}")
        cleanup_orders(info, trader)
        return None

    second_status = place_limit_order(trader, second_action)
    return resolve_rebuild_result(
        info, trader, reference_price, buy_action, sell_action, second_action, second_status
    )

def rebuild(info, trader, live_snapshot, strategy="reset", rebuild_price=None):
    if strategy in ("done_deal", "anchor_break"):
        if live_snapshot["mode"] in (BUY_ONLY_MODE, SELL_ONLY_MODE):
            if rebuild_price is None:
                rebuild_price = read_btc_grid(info)
            return place_done_deal_rebuild(info, trader, live_snapshot["orders"][0], rebuild_price)

    if live_snapshot["orders"] and not cleanup_orders(info, trader, live_snapshot["orders"]):
        return None

    return place_reset_rebuild(info, trader, read_btc_grid(info))
