# minimal_hl_spot_buy.py
# pip install hyperliquid-python-sdk python-dotenv eth-account

import os
import time
from dotenv import load_dotenv
from hyperliquid.utils import constants
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from eth_account import Account

load_dotenv()

API_KEY = os.getenv("HYPERLIQUID_API_KEY")
ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")

SPOT_COIN = "UBTC/USDC"
BUDGET_USDC = 10.0
DISCOUNT_PCT = 0.999        # 99.5% of mid price
SIZE_DECIMALS = 5
POLL_INTERVAL_SEC = 2
MAX_POLLS = 15              # total watch time = 30 sec


def fmt_num(x, digits=8):
    if x is None:
        return "N/A"
    return f"{x:.{digits}f}"


def get_mid_price(info: Info) -> float:
    all_mids = info.all_mids()
    return float(all_mids.get("@142", 0))


def extract_order_summary(order_status: dict):
    """
    Try to extract a simplified human-readable status from query_order_by_oid payload.
    Returns:
        lifecycle_status: str
        limit_px: float | None
        size: float | None
        side: str | None
    """
    try:
        order_block = order_status.get("order", {})
        inner_order = order_block.get("order", {})
        lifecycle_status = order_block.get("status", "unknown")
        limit_px = float(inner_order["limitPx"]) if "limitPx" in inner_order else None
        size = float(inner_order["sz"]) if "sz" in inner_order else None
        side = inner_order.get("side")
        return lifecycle_status, limit_px, size, side
    except Exception:
        return "unknown", None, None, None


def status_badge(status: str) -> str:
    s = (status or "").lower()
    if s == "open":
        return "OPEN"
    if s == "filled":
        return "FILLED"
    if s == "canceled":
        return "CANCELED"
    if s == "rejected":
        return "REJECTED"
    return s.upper() if s else "UNKNOWN"


def main():
    if not API_KEY or not ACCOUNT_ADDRESS:
        print("[ERROR ] Missing HYPERLIQUID_API_KEY or HYPERLIQUID_ACCOUNT_ADDRESS in .env")
        return

    # Init clients
    agent = Account.from_key(API_KEY)
    info = Info(constants.MAINNET_API_URL)
    exchange = Exchange(
        agent,
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS
    )

    # 1) Get current BTC mid price
    btc_mid = get_mid_price(info)
    if btc_mid <= 0:
        print("[ERROR ] Failed to get BTC mid price")
        return

    # 2) Build a minimal test buy order
    limit_price = float(int(btc_mid * DISCOUNT_PCT))
    btc_size = round(BUDGET_USDC / limit_price, SIZE_DECIMALS)

    if btc_size <= 0:
        print("[ERROR ] Computed BTC size <= 0")
        return

    print("==================================================")
    print("[SETUP ] Minimal Hyperliquid spot limit buy test")
    print(f"[MARKET] BTC mid        = {fmt_num(btc_mid, 2)}")
    print(f"[ORDER ] Side          = BUY")
    print(f"[ORDER ] Spot pair     = {SPOT_COIN}")
    print(f"[ORDER ] Budget USDC   = {fmt_num(BUDGET_USDC, 2)}")
    print(f"[ORDER ] Limit price   = {fmt_num(limit_price, 2)}")
    print(f"[ORDER ] BTC size      = {fmt_num(btc_size, 5)}")
    print("==================================================")

    # 3) Submit order
    try:
        result = exchange.order(
            SPOT_COIN,
            True,                         # buy
            btc_size,
            limit_price,
            {"limit": {"tif": "Gtc"}},
            False                         # reduce_only must be False for spot
        )
    except Exception as e:
        print(f"[ERROR ] Order submission failed: {e}")
        return

    if result.get("status") != "ok":
        print("[ERROR ] API call returned non-ok status")
        print(result)
        return

    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        print("[ERROR ] No statuses found in order response")
        print(result)
        return

    first_status = statuses[0]

    # Case A: immediately filled
    if "filled" in first_status:
        print("[DONE  ] Order filled immediately ✅")
        print(first_status["filled"])
        return

    # Case B: resting
    if "resting" not in first_status:
        print("[WARN  ] Unrecognized initial order status:")
        print(first_status)
        return

    oid = first_status["resting"]["oid"]

    print(f"[SUBMIT] Order accepted, now resting on book")
    print(f"[INFO  ] OID           = {oid}")
    print(f"[WATCH ] Poll interval = {POLL_INTERVAL_SEC}s")
    print(f"[WATCH ] Max polls     = {MAX_POLLS}")
    print("--------------------------------------------------")

    last_lifecycle_status = None

    for i in range(1, MAX_POLLS + 1):
        time.sleep(POLL_INTERVAL_SEC)

        try:
            order_status = info.query_order_by_oid(ACCOUNT_ADDRESS, oid)
        except Exception as e:
            print(f"[ERROR ] Poll {i:02d}: query failed: {e}")
            continue

        lifecycle_status, order_limit_px, order_size, side = extract_order_summary(order_status)
        badge = status_badge(lifecycle_status)

        current_mid = get_mid_price(info)
        distance = None
        if current_mid > 0 and order_limit_px is not None:
            distance = current_mid - order_limit_px

        # Only emphasize lifecycle change once
        if lifecycle_status != last_lifecycle_status:
            print(f"[STATUS] {badge}")
            last_lifecycle_status = lifecycle_status

        # Human-friendly summary line
        side_label = "BUY" if side == "B" else ("SELL" if side == "A" else "UNKNOWN")

        if lifecycle_status == "open":
            print(
                f"[WAIT  ] Poll {i:02d} | {side_label} {fmt_num(order_size, 5)} BTC"
                f" | limit={fmt_num(order_limit_px, 2)}"
                f" | mid={fmt_num(current_mid, 2)}"
                f" | mid-limit={fmt_num(distance, 2)}"
            )
            continue

        if lifecycle_status == "filled":
            print(
                f"[FILLED] Poll {i:02d} | {side_label} {fmt_num(order_size, 5)} BTC"
                f" @ {fmt_num(order_limit_px, 2)} ✅"
            )
            print("--------------------------------------------------")
            print("[DONE  ] Successful done deal")
            return

        if lifecycle_status == "canceled":
            print(f"[CANCEL] Poll {i:02d} | Order canceled")
            print("--------------------------------------------------")
            return

        if lifecycle_status == "rejected":
            print(f"[REJECT] Poll {i:02d} | Order rejected")
            print("--------------------------------------------------")
            return

        print(
            f"[INFO  ] Poll {i:02d} | status={badge}"
            f" | limit={fmt_num(order_limit_px, 2)}"
            f" | mid={fmt_num(current_mid, 2)}"
        )

    print("--------------------------------------------------")
    print("[TIMEOUT] Watch window ended, order still not filled")
    print("[NOTE  ] The order may still be resting on the book")


if __name__ == "__main__":
    main()