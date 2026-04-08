# grid_runner_min.py, pip3 install hyperliquid-python-sdk
 
import os
import time

from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_KEY = os.getenv("HYPERLIQUID_API_KEY")

SYMBOL = "UBTC/USDC"
BTC_MID_KEY = "@142"

GRID_STEP = 50.0
BUDGET_USDC = 50.0
PAIR_PRICE_TOLERANCE = 0.1
ALLOW_BUY_ONLY_WHEN_NO_BTC = True
REANCHOR_BREAK = False
REANCHOR_BREAK_STEPS = 2

PAIR_MODE = "PAIR"
BUY_ONLY_MODE = "BUY_ONLY"
MAX_ABNORMAL_COUNT = 3

from grid_logic import (
    get_pair_state,
    pair_matches_state,
    should_reanchor_residual_order,
)


def get_open_orders(info):
    return info.open_orders(ACCOUNT_ADDRESS)


def summarize_orders(orders):
    if not orders:
        print("OPEN ORDERS: none")
        return 0, 0

    parts = []
    buy_count = 0
    sell_count = 0
    for order in orders:
        side = "BUY" if order["side"] == "B" else "SELL"
        parts.append(f"{side} @{order['limitPx']} size={order['sz']} oid={order['oid']}")
        if order["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    print("OPEN ORDERS:", " | ".join(parts))
    return buy_count, sell_count


def get_mid_reference_price(info):
    btc_mid = float(info.all_mids().get(BTC_MID_KEY, 0))
    if btc_mid <= 0:
        raise ValueError("Failed to get BTC mid price")

    reference_price = float(int(btc_mid // GRID_STEP) * GRID_STEP)
    if reference_price <= 0:
        raise ValueError("Invalid reference price")
    if reference_price - GRID_STEP <= 0:
        raise ValueError("Invalid buy price")

    return reference_price


def build_pair(reference_price):
    if reference_price <= 0:
        raise ValueError("Invalid reference price")

    buy_price = reference_price - GRID_STEP
    if buy_price <= 0:
        raise ValueError("Invalid buy price")

    size = round(BUDGET_USDC / reference_price, 5)
    return (
        {"side": "BUY", "price": buy_price, "size": size},
        {"side": "SELL", "price": reference_price + GRID_STEP, "size": size},
    )


def classify_order_result(result):
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        print(f"order failed: {result}")
        return "error"

    status = statuses[0]
    if "error" in status:
        error = status["error"]
        print(f"order failed: {error}")
        if "Insufficient spot balance" in error:
            return "insufficient_spot_balance"
        return "error"

    if "resting" in status or "filled" in status:
        return "ok"

    print(f"order failed: {result}")
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


def wait_no_open_orders(info, max_tries=10, interval_sec=1.0):
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

    print("cleanup failure: open orders still remain")
    return False


def place_pair(exchange, reference_price):
    buy_action, sell_action = build_pair(reference_price)
    print(
        f"rebuilding: reference_price={reference_price} "
        f"buy={buy_action['price']} sell={sell_action['price']}"
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
        print("buy-only fallback")
        return {
            "mode": BUY_ONLY_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    return None


def rebuild(info, exchange, orders, reference_price=None):
    if orders and not cleanup_orders(info, exchange, orders):
        return None

    if reference_price is None:
        reference_price = get_mid_reference_price(info)

    state = place_pair(exchange, reference_price)
    if state is not None:
        return state

    orders = get_open_orders(info)
    if orders and not cleanup_orders(info, exchange, orders):
        return None

    return None


def get_loop_action(info, orders, state):
    buy_count = 0
    sell_count = 0
    for order in orders:
        if order["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    if buy_count == 1 and sell_count == 1:
        current_pair = get_pair_state(orders, GRID_STEP, PAIR_PRICE_TOLERANCE, PAIR_MODE)
        if current_pair is None:
            return "abnormal", None
        if not pair_matches_state(current_pair, state, PAIR_PRICE_TOLERANCE):
            return "abnormal", None
        if state["mode"] == BUY_ONLY_MODE:
            state["mode"] = PAIR_MODE
            print("pair valid")
        return "keep", None

    if state["mode"] == PAIR_MODE:
        if buy_count == 0 and sell_count == 1:
            if should_reanchor_residual_order(
                info,
                orders,
                BTC_MID_KEY,
                GRID_STEP,
                REANCHOR_BREAK,
                REANCHOR_BREAK_STEPS,
            ):
                return "rebuild", None
            print("🔥 fill detected: BUY filled")
            return "rebuild", state["buy_price"]

        if buy_count == 1 and sell_count == 0:
            if should_reanchor_residual_order(
                info,
                orders,
                BTC_MID_KEY,
                GRID_STEP,
                REANCHOR_BREAK,
                REANCHOR_BREAK_STEPS,
            ):
                return "rebuild", None
            print("✅ fill detected: SELL filled")
            return "rebuild", state["sell_price"]

        return "abnormal", None

    if buy_count == 1 and sell_count == 0:
        if should_reanchor_residual_order(
            info,
            orders,
            BTC_MID_KEY,
            GRID_STEP,
            REANCHOR_BREAK,
            REANCHOR_BREAK_STEPS,
        ):
            return "rebuild", None
        return "keep", None

    if buy_count == 0 and sell_count == 0:
        print("fill detected: BUY filled")
        return "rebuild", state["buy_price"]

    return "abnormal", None


def run_main_loop(info, exchange, state):
    abnormal_count = 0

    while True:
        time.sleep(1.0)
        orders = get_open_orders(info)
        action, reference_price = get_loop_action(info, orders, state)

        if action == "keep":
            abnormal_count = 0
            continue

        if action == "rebuild":
            state = rebuild(info, exchange, orders, reference_price)
            if state is None:
                return
            abnormal_count = 0
            continue

        abnormal_count += 1
        buy_count, sell_count = summarize_orders(orders)
        print(f"abnormal state {abnormal_count}/{MAX_ABNORMAL_COUNT}: buy={buy_count} sell={sell_count}")
        if abnormal_count >= MAX_ABNORMAL_COUNT:
            print("abnormal exit")
            return


def create_exchange():
    if not API_KEY:
        raise ValueError("Missing HYPERLIQUID_API_KEY")

    return Exchange(
        Account.from_key(API_KEY),
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )


def main():
    if not ACCOUNT_ADDRESS:
        print("Missing HYPERLIQUID_ACCOUNT_ADDRESS")
        return

    info = Info(constants.MAINNET_API_URL)
    exchange = create_exchange()

    orders = get_open_orders(info)
    buy_count, sell_count = summarize_orders(orders)

    state = None
    if buy_count == 1 and sell_count == 1:
        state = get_pair_state(orders, GRID_STEP, PAIR_PRICE_TOLERANCE, PAIR_MODE)
        if state is not None:
            print("pair valid")

    if state is None:
        state = rebuild(info, exchange, orders)
        if state is None:
            return

    run_main_loop(info, exchange, state)


if __name__ == "__main__":
    main()
