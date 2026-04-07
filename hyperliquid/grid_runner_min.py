"""
grid_runner_min.py

Responsibility
This module runs a live Hyperliquid spot grid loop for `UBTC/USDC`. It maintains a
two-sided grid when both orders can rest, and it degrades to buy-only mode only when
the sell order is rejected for insufficient spot balance and the fallback flag is enabled.

Used by:
* (no direct callers found)

Pipelines:
- env -> bootstrap -> inspect -> classify -> build -> place -> monitor -> rebuild

Invariants
- Runtime state must record whether open orders are interpreted as `PAIR` or `BUY_ONLY`.
- Pair mode only treats `1 BUY + 1 SELL` as the steady state.
- Buy-only mode only treats `1 BUY + 0 SELL` as the steady state.
- Buy-only fallback is only entered when the sell placement failure reason is insufficient spot balance.

Out of scope
- Pre-checking balances before submitting orders.
- Persisting runtime mode across process restarts.
- Recovering from non-balance exchange failures beyond logging, cleanup, and exit.
"""

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

GRID_STEP = 100.0
BUDGET_USDC = 100.0
DRY_RUN = False
CHECK_PAIR_SHAPE = False
PAIR_PRICE_TOLERANCE = 0.1
ALLOW_BUY_ONLY_WHEN_NO_BTC = True

PAIR_MODE = "PAIR"
BUY_ONLY_MODE = "BUY_ONLY"


def get_open_orders(info):
    """Return the current open spot orders for the configured account."""
    return info.open_orders(ACCOUNT_ADDRESS)


def get_btc_mid_price(info):
    """Return the positive BTC mid price used to anchor a new grid reference."""
    px = float(info.all_mids().get(BTC_MID_KEY, 0))
    if px <= 0:
        raise ValueError("Failed to get BTC mid price")
    return px


def count_orders(orders):
    """Return `(buy_count, sell_count)` for an open-order snapshot."""
    buy_count = 0
    sell_count = 0

    for o in orders:
        if o["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    return buy_count, sell_count


def summarize_orders(orders):
    """Print the open-order snapshot and return `(buy_count, sell_count)`."""
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
    """
    Purpose:
    Build the target buy and sell limit-order actions for one grid step around a reference price.
    Inputs:
    - info: Hyperliquid `Info` client instance.
    - reference_price: Optional float reference price. When omitted, the current BTC mid is rounded down to the grid.
    Outputs:
    - Two-item list containing a buy action and a sell action.
    """
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


def classify_order_result(result):
    """
    Purpose:
    Classify an exchange order response into success or a specific failure reason.
    Inputs:
    - result: Raw response object returned by `exchange.order`.
    Outputs:
    - Dict with `ok` plus either a success `state` or a failure `reason`.
    """
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        print(f"ORDER FAILED: unknown response {result}")
        return {"ok": False, "reason": "unknown"}

    first = statuses[0]
    if "error" in first:
        error = first["error"]
        print(f"ORDER FAILED: {error}")
        if "Insufficient spot balance" in error:
            return {"ok": False, "reason": "insufficient_spot_balance", "error": error}
        return {"ok": False, "reason": "exchange_error", "error": error}

    if "resting" in first or "filled" in first:
        state = "resting" if "resting" in first else "filled"
        return {"ok": True, "state": state}

    print(f"ORDER FAILED: unknown response {result}")
    return {"ok": False, "reason": "unknown"}


def place_limit_order(exchange, action):
    """
    Purpose:
    Submit one limit order action unless dry run mode is enabled.
    Inputs:
    - exchange: Hyperliquid `Exchange` client instance.
    - action: Dict containing `side`, `price`, and `size`.
    Outputs:
    - Dict describing whether the order submission succeeded and why.
    """
    side = action["side"]
    price = float(action["price"])
    size = float(action["size"])

    if DRY_RUN:
        print(f"WOULD PLACE {side} @ {price} size={size}")
        return {"ok": True, "state": "dry_run"}

    result = exchange.order(
        SYMBOL,
        side == "BUY",
        size,
        price,
        {"limit": {"tif": "Gtc"}},
        False,
    )
    print(result)
    return classify_order_result(result)


def bulk_cancel_all(exchange, orders):
    """
    Purpose:
    Cancel every open order in the provided snapshot.
    Inputs:
    - exchange: Hyperliquid `Exchange` client instance.
    - orders: Iterable of Hyperliquid open order objects.
    Outputs:
    - None.
    """
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
    """
    Purpose:
    Poll until the account has no open orders or the retry limit is reached.
    Inputs:
    - info: Hyperliquid `Info` client instance.
    - max_tries: Maximum number of polling attempts.
    - interval_sec: Sleep interval between polling attempts.
    Outputs:
    - `True` when no open orders remain, otherwise `False`.
    """
    for i in range(max_tries):
        orders = get_open_orders(info)
        print(f"check {i+1}/{max_tries}: open_orders={len(orders)}")
        if not orders:
            return True
        time.sleep(interval_sec)
    return False


def get_pair_state(orders):
    """
    Purpose:
    Derive a valid two-sided pair state from an open-order snapshot.
    Inputs:
    - orders: Iterable of Hyperliquid open order objects.
    Outputs:
    - Dict with `buy_price`, `sell_price`, and `reference_price`, or `None` when the snapshot is not a valid pair.
    """
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

    if CHECK_PAIR_SHAPE and abs((sell_price - buy_price) - (2 * GRID_STEP)) > PAIR_PRICE_TOLERANCE:
        return None

    reference_price = (buy_price + sell_price) / 2.0

    return {
        "buy_price": buy_price,
        "sell_price": sell_price,
        "reference_price": reference_price,
    }


def pair_matches_state(current_pair_state, pair_state):
    """
    Purpose:
    Check whether a live pair snapshot matches the tracked runtime pair within tolerance.
    Inputs:
    - current_pair_state: Pair-state dict derived from live open orders.
    - pair_state: Tracked runtime state dict.
    Outputs:
    - `True` when buy and sell prices match within tolerance, otherwise `False`.
    """
    if current_pair_state is None or pair_state is None:
        return False

    if abs(current_pair_state["buy_price"] - pair_state["buy_price"]) > PAIR_PRICE_TOLERANCE:
        return False

    if abs(current_pair_state["sell_price"] - pair_state["sell_price"]) > PAIR_PRICE_TOLERANCE:
        return False

    return True


def build_runtime_state(actions, reference_price, mode):
    """
    Purpose:
    Build the in-memory state object used by the main loop to interpret open orders.
    Inputs:
    - actions: Two-item list containing the current buy and sell actions.
    - reference_price: Float reference price associated with the current grid state.
    - mode: Runtime mode string, either `PAIR` or `BUY_ONLY`.
    Outputs:
    - Dict with runtime mode and tracked order prices. In `BUY_ONLY` mode, `sell_price`
      remains the target grid sell reference even when no live sell order is resting.
    """
    return {
        "mode": mode,
        "buy_price": float(actions[0]["price"]),
        "sell_price": float(actions[1]["price"]),
        "reference_price": reference_price,
    }


def place_pair(exchange, actions):
    """
    Purpose:
    Submit the current buy and sell actions and classify the resulting runtime mode.
    Inputs:
    - exchange: Hyperliquid `Exchange` client instance.
    - actions: Two-item list containing the current buy and sell actions.
    Outputs:
    - Dict describing per-side order results, overall success, and the resulting runtime mode.
    """
    print()
    print("=== ACTIONS ===")
    for a in actions:
        print(a)

    print()
    print("=== EXECUTE ===")
    placement = {
        "ok": False,
        "mode": None,
        "buy_ok": False,
        "sell_ok": False,
        "buy_result": None,
        "sell_result": None,
    }
    for a in actions:
        result = place_limit_order(exchange, a)
        if a["side"] == "BUY":
            placement["buy_result"] = result
            placement["buy_ok"] = result["ok"]
        else:
            placement["sell_result"] = result
            placement["sell_ok"] = result["ok"]

    if placement["buy_ok"] and placement["sell_ok"]:
        placement["ok"] = True
        placement["mode"] = PAIR_MODE
        return placement

    if (
        placement["buy_ok"]
        and not placement["sell_ok"]
        and placement["sell_result"] is not None
        and placement["sell_result"].get("reason") == "insufficient_spot_balance"
        and ALLOW_BUY_ONLY_WHEN_NO_BTC
    ):
        placement["ok"] = True
        placement["mode"] = BUY_ONLY_MODE
        print("STATUS: SELL rejected due to insufficient BTC, fallback to BUY_ONLY")
        return placement

    return placement


def cleanup_failed_pair(info, exchange):
    """
    Purpose:
    Cancel residual open orders after a pair placement attempt failed.
    Inputs:
    - info: Hyperliquid `Info` client instance.
    - exchange: Hyperliquid `Exchange` client instance.
    Outputs:
    - None.
    """
    print("ERROR: pair placement failed")
    orders = get_open_orders(info)
    if not orders:
        return

    bulk_cancel_all(exchange, orders)
    if not wait_no_open_orders(info):
        print("ERROR: open orders still remain after failed pair placement")


def run_main_loop(info, exchange, pair_state):
    """
    Purpose:
    Monitor open orders, detect fills according to the current runtime mode, and rebuild the grid.
    Inputs:
    - info: Hyperliquid `Info` client instance.
    - exchange: Hyperliquid `Exchange` client instance.
    - pair_state: Runtime state dict containing mode and tracked prices.
    Outputs:
    - None.
    """
    print()
    print("=== MAIN LOOP ===")
    abnormal_count = 0

    while True:
        time.sleep(1.0)
        orders = get_open_orders(info)
        buy_count, sell_count = count_orders(orders)
        mode = pair_state["mode"]

        if mode == BUY_ONLY_MODE:
            if buy_count == 1 and sell_count == 0:
                abnormal_count = 0
                continue

            if buy_count == 1 and sell_count == 1:
                current_pair_state = get_pair_state(orders)
                if current_pair_state is not None and pair_matches_state(current_pair_state, pair_state):
                    pair_state["mode"] = PAIR_MODE
                    abnormal_count = 0
                    print("STATUS: recovered to PAIR mode")
                    continue

            if buy_count == 0 and sell_count == 0:
                filled_order_side = "BUY"
                reference_price = pair_state["buy_price"]
            else:
                abnormal_count += 1
                print(
                    "LOOP STATUS: buy-only mode "
                    f"buy={buy_count} sell={sell_count} abnormal={abnormal_count} orders={orders}"
                )
                if abnormal_count >= 3:
                    print(
                        "ERROR: repeated abnormal buy-only order state "
                        f"buy={buy_count} sell={sell_count} orders={orders}"
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
            placement = place_pair(exchange, actions)
            if not placement["ok"]:
                cleanup_failed_pair(info, exchange)
                return

            pair_state = build_runtime_state(actions, reference_price, placement["mode"])
            continue

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
        placement = place_pair(exchange, actions)
        if not placement["ok"]:
            cleanup_failed_pair(info, exchange)
            return

        pair_state = build_runtime_state(actions, reference_price, placement["mode"])


def create_exchange():
    """
    Purpose:
    Create the authenticated Hyperliquid exchange client for live order placement.
    Inputs:
    - None.
    Outputs:
    - Hyperliquid `Exchange` client instance.
    """
    if not API_KEY:
        raise ValueError("Missing HYPERLIQUID_API_KEY")

    agent = Account.from_key(API_KEY)
    return Exchange(
        agent,
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )


def main():
    """
    Purpose:
    Bootstrap clients, normalize the starting order state, and start the live grid loop.
    Inputs:
    - None.
    Outputs:
    - None.
    """
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
        pair_state["mode"] = PAIR_MODE
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
        placement = place_pair(exchange, actions)
        if not placement["ok"]:
            cleanup_failed_pair(info, exchange)
            return
        pair_state = build_runtime_state(
            actions,
            float(actions[0]["price"]) + GRID_STEP,
            placement["mode"],
        )

    if DRY_RUN:
        print("DRY_RUN: skip live main loop")
        return

    run_main_loop(info, exchange, pair_state)


if __name__ == "__main__":
    main()
