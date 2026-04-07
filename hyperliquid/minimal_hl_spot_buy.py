# minimal_hl_spot_buy.py
# pip install hyperliquid-python-sdk python-dotenv eth-account

import os
from dotenv import load_dotenv
from hyperliquid.utils import constants
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from eth_account import Account

load_dotenv()

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

    # 1. 取 BTC 现价
    all_mids = info.all_mids()
    btc_price = float(all_mids.get("@142", 0))

    if btc_price <= 0:
        print("Failed to get BTC price")
        return

    # 2. 设置最小测试参数
    budget_usdc = 30.0
    limit_price = float(int(btc_price * 0.995))   # 略低于市价挂买单
    btc_size = round(budget_usdc / limit_price, 5)

    print("BTC mid price:", btc_price)
    print("Limit price:", limit_price)
    print("BTC size:", btc_size)

    # 3. 下最简单的现货限价买单
    result = exchange.order(
        "UBTC/USDC",                  # 现货对
        True,                         # buy
        btc_size,                     # size
        limit_price,                  # limit price
        {"limit": {"tif": "Gtc"}},    # Good till cancel
        False                         # reduce_only must be False for spot
    )

    print(result)

if __name__ == "__main__":
    main()