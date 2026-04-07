# grid_runner_min.py
# pip install hyperliquid-python-sdk python-dotenv eth-account

import os
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


def get_btc_mid_price(info):
    all_mids = info.all_mids()
    btc_price = float(all_mids.get(BTC_MID_KEY, 0))
    if btc_price <= 0:
        raise ValueError("Failed to get BTC mid price")
    return btc_price


def build_actions(info, buy_orders, sell_orders, grid_step, budget_usdc):
    if buy_orders and sell_orders:
        print("STATUS: both sides present")
        return []

    if buy_orders and not sell_orders:
        buy_px = float(buy_orders[0]["limitPx"])
        buy_sz = float(buy_orders[0]["sz"])

        print("STATUS: missing sell")
        return [
            {
                "side": "SELL",
                "price": buy_px + 2 * grid_step,
                "size": buy_sz,
            }
        ]

    if sell_orders and not buy_orders:
        sell_px = float(sell_orders[0]["limitPx"])
        sell_sz = float(sell_orders[0]["sz"])

        print("STATUS: missing buy")
        return [
            {
                "side": "BUY",
                "price": sell_px - 2 * grid_step,
                "size": sell_sz,
            }
        ]

    print("STATUS: no orders")
    btc_mid = get_btc_mid_price(info)
    reference_price = float(int(btc_mid // grid_step) * grid_step)

    if reference_price <= 0:
        raise ValueError("Invalid reference price")

    size = round(budget_usdc / reference_price, 5)

    print(f"BTC MID PRICE: {btc_mid}")
    print(f"REFERENCE PRICE: {reference_price}")

    return [
        {
            "side": "BUY",
            "price": reference_price - grid_step,
            "size": size,
        },
        {
            "side": "SELL",
            "price": reference_price + grid_step,
            "size": size,
        },
    ]


def place_limit_order(exchange, symbol, action, dry_run=True):
    side = action["side"]
    price = float(action["price"])
    size = float(action["size"])

    if dry_run:
        print(f"WOULD PLACE {side} @ {price} size={size}")
        return

    is_buy = side == "BUY"

    result = exchange.order(
        symbol,
        is_buy,
        size,
        price,
        {"limit": {"tif": "Gtc"}},
        False
    )

    print(result)


def create_exchange(api_key, account_address):
    if not api_key:
        raise ValueError("Missing HYPERLIQUID_API_KEY")

    agent = Account.from_key(api_key)
    return Exchange(
        agent,
        constants.MAINNET_API_URL,
        account_address=account_address
    )


def main():
    if not ACCOUNT_ADDRESS:
        print("Missing HYPERLIQUID_ACCOUNT_ADDRESS")
        return

    info = Info(constants.MAINNET_API_URL)
    orders = get_open_orders(info, ACCOUNT_ADDRESS)
    buy_orders, sell_orders = summarize_orders(orders)

    try:
        actions = build_actions(
            info=info,
            buy_orders=buy_orders,
            sell_orders=sell_orders,
            grid_step=GRID_STEP,
            budget_usdc=BUDGET_USDC,
        )
    except Exception as e:
        print(f"ERROR: {e}")
        return

    if not actions:
        return

    print()
    print("=== ACTIONS ===")
    for action in actions:
        print(action)

    if DRY_RUN:
        print()
        print("=== DRY RUN ===")
        for action in actions:
            place_limit_order(None, SYMBOL, action, dry_run=True)
        return

    try:
        exchange = create_exchange(API_KEY, ACCOUNT_ADDRESS)
    except Exception as e:
        print(f"ERROR: {e}")
        return

    print()
    print("=== EXECUTE ===")
    for action in actions:
        place_limit_order(exchange, SYMBOL, action, dry_run=False)


if __name__ == "__main__":
    main()