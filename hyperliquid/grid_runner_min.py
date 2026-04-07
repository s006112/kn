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

GRID_STEP = 600.0
BUDGET_USDC = 10.0
DRY_RUN = False


def get_open_orders(info):
    return info.open_orders(ACCOUNT_ADDRESS)


def get_btc_mid_price(info):
    px = float(info.all_mids().get(BTC_MID_KEY, 0))
    if px <= 0:
        raise ValueError("Failed to get BTC mid price")
    return px


def summarize_orders(orders):
    buy_count = 0
    sell_count = 0

    print("=== OPEN ORDERS ===")
    if not orders:
        print("none")
        return 0, 0

    for o in orders:
        side = "BUY" if o["side"] == "B" else "SELL"
        print(f"{side} @ {o['limitPx']} size={o['sz']} oid={o['oid']}")
        if o["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    print()
    print(f"BUY ORDERS: {buy_count}")
    print(f"SELL ORDERS: {sell_count}")
    return buy_count, sell_count


def build_pair_actions(info):
    btc_mid = get_btc_mid_price(info)
    reference_price = float(int(btc_mid // GRID_STEP) * GRID_STEP)
    size = round(BUDGET_USDC / reference_price, 5)

    print(f"BTC MID PRICE: {btc_mid}")
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

    orders = get_open_orders(info)
    buy_count, sell_count = summarize_orders(orders)

    if buy_count == 1 and sell_count == 1:
        print("STATUS: valid pair, keep")
        return

    if orders:
        print("STATUS: abnormal/orphan orders, cancel all and rebuild")
        bulk_cancel_all(exchange, orders)

        if not DRY_RUN:
            if not wait_no_open_orders(info):
                print("ERROR: open orders still remain after bulk cancel")
                return

    else:
        print("STATUS: no orders, rebuild")

    print()
    print("=== ACTIONS ===")
    actions = build_pair_actions(info)
    for a in actions:
        print(a)

    print()
    print("=== EXECUTE ===")
    for a in actions:
        place_limit_order(exchange, a)


if __name__ == "__main__":
    main()