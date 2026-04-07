# grid_runner_min.py

import os
from dotenv import load_dotenv
from hyperliquid.utils import constants
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from eth_account import Account

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_KEY = os.getenv("HYPERLIQUID_API_KEY")

GRID_STEP = 500.0
DRY_RUN = False
SYMBOL = "UBTC/USDC"


def get_open_orders(info, account_address):
    return info.open_orders(account_address)


def summarize_orders(orders):
    buy_orders = []
    sell_orders = []

    print("=== OPEN ORDERS ===")

    if not orders:
        print("none")
        return buy_orders, sell_orders

    for o in orders:
        side = "BUY" if o["side"] == "B" else "SELL"
        print(f"{side} @ {o['limitPx']} size={o['sz']} oid={o['oid']}")

        if o["side"] == "B":
            buy_orders.append(o)
        else:
            sell_orders.append(o)

    print()
    print(f"BUY ORDERS: {len(buy_orders)}")
    print(f"SELL ORDERS: {len(sell_orders)}")

    return buy_orders, sell_orders


def build_missing_side_action(buy_orders, sell_orders, grid_step):
    if buy_orders and sell_orders:
        print("STATUS: both sides present")
        return None

    if buy_orders and not sell_orders:
        buy_px = float(buy_orders[0]["limitPx"])
        buy_sz = float(buy_orders[0]["sz"])
        action = {
            "side": "SELL",
            "price": buy_px + 2 * grid_step,
            "size": buy_sz,
        }
        print("STATUS: missing sell")
        return action

    if sell_orders and not buy_orders:
        sell_px = float(sell_orders[0]["limitPx"])
        sell_sz = float(sell_orders[0]["sz"])
        action = {
            "side": "BUY",
            "price": sell_px - 2 * grid_step,
            "size": sell_sz,
        }
        print("STATUS: missing buy")
        return action

    print("STATUS: no orders")
    return None


def place_limit_order(exchange, action, dry_run=True):
    if not action:
        return

    side = action["side"]
    price = float(action["price"])
    size = float(action["size"])

    if dry_run:
        print(f"WOULD PLACE {side} @ {price} size={size}")
        return

    is_buy = side == "BUY"
    result = exchange.order(
        SYMBOL,
        is_buy,
        size,
        price,
        {"limit": {"tif": "Gtc"}},
        False
    )
    print(result)


def main():
    if not ACCOUNT_ADDRESS:
        print("Missing HYPERLIQUID_ACCOUNT_ADDRESS")
        return

    info = Info(constants.MAINNET_API_URL)
    orders = get_open_orders(info, ACCOUNT_ADDRESS)
    buy_orders, sell_orders = summarize_orders(orders)
    action = build_missing_side_action(buy_orders, sell_orders, GRID_STEP)

    if not action:
        return

    print(f"ACTION: {action}")

    if DRY_RUN:
        place_limit_order(None, action, dry_run=True)
        return

    if not API_KEY:
        print("Missing HYPERLIQUID_API_KEY")
        return

    agent = Account.from_key(API_KEY)
    exchange = Exchange(
        agent,
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS
    )
    place_limit_order(exchange, action, dry_run=False)


if __name__ == "__main__":
    main()