import os

from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")  # likely unchange unless you want to test with a different account
API_WALLET_KEY = os.getenv("HYPERLIQUID_API_WALLET_KEY")    # hyperliquid only show 1 time
SYMBOL = os.getenv("SYMBOL", "UBTC/USDC")


def die(msg):
    raise SystemExit(f"ERROR: {msg}")


def main():
    if not ACCOUNT_ADDRESS:
        die("missing HYPERLIQUID_ACCOUNT_ADDRESS")
    if not API_WALLET_KEY:
        die("missing HYPERLIQUID_API_WALLET_KEY")

    signer = Account.from_key(API_WALLET_KEY)

    print("ACCOUNT_ADDRESS:", ACCOUNT_ADDRESS)
    print("SIGNER_ADDRESS :", signer.address)
    print("SYMBOL         :", SYMBOL)

    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    trader = Exchange(
        signer,
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )

    orders = info.open_orders(ACCOUNT_ADDRESS)
    print("READ_OPEN_ORDERS: OK")
    print("OPEN_ORDERS     :", len(orders))

    result = trader.order(
        SYMBOL,
        True,
        0.0,
        1.0,
        {"limit": {"tif": "Gtc"}},
        False,
    )

    print("SIGNED_PROBE_RESULT:", repr(result))

    text = repr(result)
    if "does not exist" in text:
        die("API wallet is not authorized / not activated")

    if "Order has zero size" not in text:
        die("unexpected signed probe result")

    print("SIGNED_EXCHANGE_ACCESS: OK")
    print("NO REAL ORDER / NO CANCEL / NO MODIFY expected")


if __name__ == "__main__":
    main()