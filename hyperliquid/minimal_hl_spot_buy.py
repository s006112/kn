# minimal_hl_spot_buy_grid_step.py
# pip install hyperliquid-python-sdk python-dotenv eth-account

import os
from dotenv import load_dotenv
from hyperliquid.utils import constants
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from eth_account import Account

load_dotenv()

grid_step = 500.0
budget_usdc = 10.0

API_KEY = os.getenv("HYPERLIQUID_API_KEY")
ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")

def main():
    if not API_KEY or not ACCOUNT_ADDRESS:
        print("Missing HYPERLIQUID_API_KEY or HYPERLIQUID_ACCOUNT_ADDRESS in .env")
        return

    agent = Account.from_key(API_KEY)
    info = Info(constants.MAINNET_API_URL)
    exchange = Exchange(
        agent,
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS
    )

    # Get current BTC price
    all_mids = info.all_mids()
    btc_price = float(all_mids.get("@142", 0))
    if btc_price <= 0:
        print("Failed to get BTC price")
        return

    # Create limit order at next nearest lower grid level
    # Example: 60387 -> 60300 when grid_step = 100
    limit_price = float(int(btc_price // grid_step) * grid_step)

    # Safety check: avoid equal/invalid price
    if limit_price <= 0:
        print("Invalid limit price")
        return

    btc_size = round(budget_usdc / limit_price, 5)

    print("BTC mid price:", btc_price)
    print("Grid step:", grid_step)
    print("Limit price:", limit_price)
    print("BTC size:", btc_size)

    result = exchange.order(
        "UBTC/USDC",
        True,
        btc_size,
        limit_price,
        {"limit": {"tif": "Gtc"}},
        False
    )

    print(result)

if __name__ == "__main__":
    main()