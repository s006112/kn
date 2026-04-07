# grid_runner_min.py
# pip install hyperliquid-python-sdk python-dotenv eth-account

import os
import time
from dotenv import load_dotenv
from hyperliquid.utils import constants
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from eth_account import Account

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_KEY = os.getenv("HYPERLIQUID_API_KEY")

SYMBOL = "UBTC/USDC"
BTC_MID_KEY = "@142"

GRID_STEP = 150.0
BUDGET_USDC = 30.0
DRY_RUN = False
PAIR_PRICE_TOLERANCE = 0.1


def get_open_orders(info):
    return info.open_orders(ACCOUNT_ADDRESS)


def get_btc_mid_price(info):
    px = float(info.all_mids().get(BTC_MID_KEY, 0))
    if px <= 0:
        raise ValueError("Failed to get BTC mid price")
    return px


def count_orders(orders):
    buy_count = 0
    sell_count = 0

    for o in orders:
        if o["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    return buy_count, sell_count


def summarize_orders(orders):
    buy_count, sell_count = count_orders(orders)

    print("=== OPEN ORDERS ===")
    if not orders:
        print("none")
        return 0, 0

    for o in orders:
        side = "BUY" if o["side"] == "B" else "SELL"
        print(f"{side} @ {o['limitPx']} size={o['sz']} oid={o['oid']}")

    print()
    print(f"BUY ORDERS: {buy_count}")
    print(f"SELL ORDERS: {sell_count}")
    return buy_count, sell_count


def build_pair_actions(info, reference_price=None):
    if reference_price is None:
        btc_mid = get_btc_mid_price(info)
        reference_price = float(int(btc_mid // GRID_STEP) * GRID_STEP)
        print(f"BTC MID PRICE: {btc_mid}")

    if reference_price <= 0:
        raise ValueError("Invalid reference price")

    if reference_price - GRID_STEP <= 0:
        raise ValueError("Invalid buy price")

    size = round(BUDGET_USDC / reference_price, 5)
    print(f"REFERENCE PRICE: {reference_price}")

    return [
        {"side": "BUY", "price": reference_price - GRID_STEP, "size": size},
        {"side": "SELL", "price": reference_price + GRID_STEP, "size": size},
    ]


def place_limit_order(exchange, action):
    side = action["side"]
    price = float(action["price"])
    size = float(action["size"])

    if DRY_RUN:
        print(f"WOULD PLACE {side} @ {price} size={size}")
        return

    result = exchange.order(
        SYMBOL,
        side == "BUY",
        size,
        price,
        {"limit": {"tif": "Gtc"}},
        False,
    )
    print(result)


def bulk_cancel_all(exchange, orders):
    if not orders:
        return

    reqs = [{"coin": SYMBOL, "oid": o["oid"]} for o in orders]

    print()
    print("=== BULK CANCEL ===")
    print(reqs)

    if DRY_RUN:
        print("WOULD BULK CANCEL")
        return

    print(exchange.bulk_cancel(reqs))


def wait_no_open_orders(info, max_tries=10, interval_sec=1.0):
    for i in range(max_tries):
        orders = get_open_orders(info)
        print(f"check {i+1}/{max_tries}: open_orders={len(orders)}")
        if not orders:
            return True
        time.sleep(interval_sec)
    return False


def get_pair_state(orders):
    buy_order = None
    sell_order = None

    for o in orders:
        if o["side"] == "B":
            if buy_order is not None:
                return None
            buy_order = o
        else:
            if sell_order is not None:
                return None
            sell_order = o

    if buy_order is None or sell_order is None:
        return None

    buy_price = float(buy_order["limitPx"])
    sell_price = float(sell_order["limitPx"])

    if buy_price >= sell_price:
        return None

    if abs((sell_price - buy_price) - (2 * GRID_STEP)) > PAIR_PRICE_TOLERANCE:
        return None

    reference_price = (buy_price + sell_price) / 2.0

    return {
        "buy_price": buy_price,
        "sell_price": sell_price,
        "reference_price": reference_price,
    }


def pair_matches_state(current_pair_state, pair_state):
    if current_pair_state is None or pair_state is None:
        return False

    if abs(current_pair_state["buy_price"] - pair_state["buy_price"]) > PAIR_PRICE_TOLERANCE:
        return False

    if abs(current_pair_state["sell_price"] - pair_state["sell_price"]) > PAIR_PRICE_TOLERANCE:
        return False

    return True


def place_pair(exchange, actions):
    print()
    print("=== ACTIONS ===")
    for a in actions:
        print(a)

    print()
    print("=== EXECUTE ===")
    for a in actions:
        place_limit_order(exchange, a)


def run_main_loop(info, exchange, pair_state):
    print()
    print("=== MAIN LOOP ===")
    abnormal_count = 0

    while True:
        time.sleep(1.0)
        orders = get_open_orders(info)
        buy_count, sell_count = count_orders(orders)

        if buy_count == 1 and sell_count == 1:
            current_pair_state = get_pair_state(orders)
            if current_pair_state is None:
                abnormal_count += 1
                print(
                    f"LOOP STATUS: invalid pair shape abnormal={abnormal_count} orders={orders}"
                )
                if abnormal_count >= 3:
                    print(f"ERROR: repeated abnormal order state buy=1 sell=1 orders={orders}")
                    return
                continue
            if not pair_matches_state(current_pair_state, pair_state):
                abnormal_count += 1
                print(
                    "LOOP STATUS: pair mismatch "
                    f"abnormal={abnormal_count} current_pair={current_pair_state} "
                    f"expected_pair={pair_state} orders={orders}"
                )
                if abnormal_count >= 3:
                    print(
                        "ERROR: repeated abnormal order state "
                        f"buy=1 sell=1 current_pair={current_pair_state} "
                        f"expected_pair={pair_state} orders={orders}"
                    )
                    return
                continue
            abnormal_count = 0
            continue

        if buy_count == 0 and sell_count == 1:
            filled_order_side = "BUY"
            reference_price = pair_state["buy_price"]
        elif buy_count == 1 and sell_count == 0:
            filled_order_side = "SELL"
            reference_price = pair_state["sell_price"]
        else:
            abnormal_count += 1
            print(
                f"LOOP STATUS: buy={buy_count} sell={sell_count} abnormal={abnormal_count} orders={orders}"
            )
            if abnormal_count >= 3:
                print(
                    f"ERROR: repeated abnormal order state buy={buy_count} sell={sell_count} orders={orders}"
                )
                return
            continue

        abnormal_count = 0
        print(f"FILLED SIDE: {filled_order_side}")
        print(f"REFERENCE PRICE: {reference_price}")

        bulk_cancel_all(exchange, orders)
        if not wait_no_open_orders(info):
            print("ERROR: open orders still remain after fill handling")
            return

        actions = build_pair_actions(info, reference_price)
        place_pair(exchange, actions)

        pair_state = {
            "buy_price": float(actions[0]["price"]),
            "sell_price": float(actions[1]["price"]),
            "reference_price": reference_price,
        }


def create_exchange():
    if not API_KEY:
        raise ValueError("Missing HYPERLIQUID_API_KEY")

    agent = Account.from_key(API_KEY)
    return Exchange(
        agent,
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )


def main():
    if not ACCOUNT_ADDRESS:
        print("Missing HYPERLIQUID_ACCOUNT_ADDRESS")
        return

    info = Info(constants.MAINNET_API_URL)
    exchange = None if DRY_RUN else create_exchange()
    pair_state = None

    orders = get_open_orders(info)
    buy_count, sell_count = summarize_orders(orders)

    if buy_count == 1 and sell_count == 1:
        pair_state = get_pair_state(orders)
        if pair_state is None:
            print("ERROR: failed to derive current pair state")
            return
        print("STATUS: valid pair, keep")

    elif orders:
        print("STATUS: abnormal/orphan orders, cancel all and rebuild")
        bulk_cancel_all(exchange, orders)

        if not DRY_RUN:
            if not wait_no_open_orders(info):
                print("ERROR: open orders still remain after bulk cancel")
                return

    else:
        print("STATUS: no orders, rebuild")

    if pair_state is None:
        actions = build_pair_actions(info)
        place_pair(exchange, actions)
        pair_state = {
            "buy_price": float(actions[0]["price"]),
            "sell_price": float(actions[1]["price"]),
            "reference_price": float(actions[0]["price"]) + GRID_STEP,
        }

    if DRY_RUN:
        print("DRY_RUN: skip live main loop")
        return

    run_main_loop(info, exchange, pair_state)


if __name__ == "__main__":
    main()
