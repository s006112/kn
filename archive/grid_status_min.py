# grid_status_min.py

import os
from dotenv import load_dotenv
from hyperliquid.utils import constants
from hyperliquid.info import Info

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
GRID_STEP = 500.0

def main():
    if not ACCOUNT_ADDRESS:
        print("Missing ACCOUNT_ADDRESS")
        return

    info = Info(constants.MAINNET_API_URL)
    orders = info.open_orders(ACCOUNT_ADDRESS)

    print("=== OPEN ORDERS ===")

    if not orders:
        print("none")
        print("STATUS: no orders")
        return

    buy_orders = []
    sell_orders = []

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

    if buy_orders and sell_orders:
        print("STATUS: both sides present")
        return

    if buy_orders:
        buy_px = float(buy_orders[0]["limitPx"])
        print("STATUS: missing sell")
        print(f"SUGGESTED SELL PRICE: {buy_px + 2 * GRID_STEP}")
        return

    if sell_orders:
        sell_px = float(sell_orders[0]["limitPx"])
        print("STATUS: missing buy")
        print(f"SUGGESTED BUY PRICE: {sell_px - 2 * GRID_STEP}")
        return

if __name__ == "__main__":
    main()