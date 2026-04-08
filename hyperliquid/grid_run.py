"""grid_run.py

Responsibility
This module runs the Hyperliquid spot grid process for `UBTC/USDC`: it loads credentials, derives or restores grid state from open orders, rebuilds paired or single-sided orders when required, and loops until repeated abnormal states or rebuild failures stop the process.

Pipelines:
- startup -> credentials -> snapshot -> validate -> rebuild
- loop -> orders -> classify -> decide -> rebuild
- actions -> submit -> classify -> state -> monitor

Invariants
- The module uses the configured constants for symbol, pricing gaps, tolerances, and mode names on every decision path.
- A rebuild returns a usable state only when order placement succeeds in `PAIR`, `BUY_ONLY`, or `SELL_ONLY` mode; abnormal or failed rebuilds stop progress.
- The main loop resets abnormal tracking after `keep` and successful `rebuild` actions and exits after `MAX_ABNORMAL_COUNT` consecutive abnormal outcomes.

Out of scope
- Defining pair-validation or residual-reanchor rules beyond the imported helper functions.
- Persisting state outside process memory.
- Recovering from exceptions raised by exchange construction, mid-price lookup, or malformed order payloads.
"""

import os
import time
from datetime import datetime

from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_KEY = os.getenv("HYPERLIQUID_API_KEY")

SYMBOL = "UBTC/USDC"
BTC_MID_KEY = "@142"

BUDGET_USDC = 100.0
GRID_STEP = 100.0
BUY_GRID_FACTOR = 1.0
SELL_GRID_FACTOR = 1.5
PAIR_PRICE_TOLERANCE = 0.1
MANUAL_PAIR_TOLERANCE = True
ALLOW_BUY_ONLY_WHEN_NO_BTC = True
ALLOW_SELL_ONLY_WHEN_NO_USDC = True
REANCHOR_BREAK = True
REANCHOR_BREAK_STEPS = 2
KEEP_LOG_INTERVAL_SEC = 300

PAIR_MODE = "PAIR"
BUY_ONLY_MODE = "BUY_ONLY"
SELL_ONLY_MODE = "SELL_ONLY"
ABNORMAL_MODE = "ABNORMAL"
MAX_ABNORMAL_COUNT = 3

last_keep_log_type = None
last_keep_log_ts = 0.0

from grid_logic import (
    classify_order_shape,
    get_pair_state,
    pair_matches_state,
    is_manual_pair_keepable,
    should_reanchor_residual_order,
)


def get_open_orders(info):
    """Purpose:
    Fetch open orders for the configured account address.

    Inputs:
    info: Hyperliquid `Info` client with `open_orders`.

    Outputs:
    Returns the result of `info.open_orders(ACCOUNT_ADDRESS)`. Propagates exceptions raised by the client call.
    """
    return info.open_orders(ACCOUNT_ADDRESS)


def log_msg(message):
    """Purpose:
    Print a timestamped log line.

    Inputs:
    message: Text appended after the current local timestamp.

    Outputs:
    Returns `None` after printing a formatted line to stdout.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def log_keep_state(keep_type, message):
    """Purpose:
    Throttle repeated keep-state log messages by type and elapsed time.

    Inputs:
    keep_type: Label used to suppress repeated logs of the same keep state.
    message: Log text emitted when the throttle allows output.

    Outputs:
    Returns `None`. Updates the module-level keep-log trackers only when `keep_type` changes or at least `KEEP_LOG_INTERVAL_SEC` seconds have elapsed.
    """
    global last_keep_log_type
    global last_keep_log_ts

    now = time.time()
    if (
        keep_type != last_keep_log_type
        or now - last_keep_log_ts >= KEEP_LOG_INTERVAL_SEC
    ):
        log_msg(message)
        last_keep_log_type = keep_type
        last_keep_log_ts = now


def summarize_orders(orders):
    """Purpose:
    Log the current open orders and count buy and sell sides.

    Inputs:
    orders: Iterable of order mappings with `side`, `limitPx`, `sz`, and `oid`.

    Outputs:
    Returns a `(buy_count, sell_count)` tuple. Logs `"OPEN ORDERS: none"` for an empty input; otherwise logs a pipe-delimited summary where side `"B"` is shown as `BUY` and every other side is shown as `SELL`.
    """
    if not orders:
        log_msg("OPEN ORDERS: none")
        return 0, 0

    parts = []
    buy_count = 0
    sell_count = 0
    for order in orders:
        side = "BUY" if order["side"] == "B" else "SELL"
        parts.append(f"{side} @{order['limitPx']} size={order['sz']} oid={order['oid']}")
        if order["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    log_msg(f"OPEN ORDERS: {' | '.join(parts)}")
    return buy_count, sell_count


def get_mid_reference_price(info):
    """Purpose:
    Derive the reference price from the current BTC mid price and configured grid step.

    Inputs:
    info: Hyperliquid `Info` client with `all_mids`.

    Outputs:
    Returns the BTC mid price floored to the nearest `GRID_STEP` as a float. Raises `ValueError` when the mid price is missing or non-positive, when the floored reference price is non-positive, or when the implied buy price would be non-positive. Propagates exceptions raised by `info.all_mids()` or float conversion.
    """
    btc_mid = float(info.all_mids().get(BTC_MID_KEY, 0))
    if btc_mid <= 0:
        raise ValueError("Failed to get BTC mid price")

    reference_price = float(int(btc_mid // GRID_STEP) * GRID_STEP)
    if reference_price <= 0:
        raise ValueError("Invalid reference price")
    if reference_price - (BUY_GRID_FACTOR * GRID_STEP) <= 0:
        raise ValueError("Invalid buy price")

    return reference_price


def build_pair(reference_price):
    """Purpose:
    Build the configured buy and sell limit-order actions around a reference price.

    Inputs:
    reference_price: Positive price anchor used to derive both order prices and shared size.

    Outputs:
    Returns a `(buy_action, sell_action)` tuple of mappings with `side`, `price`, and `size`, where size is `round(BUDGET_USDC / reference_price, 5)`. Raises `ValueError` when `reference_price` is non-positive or the computed buy price is non-positive.
    """
    if reference_price <= 0:
        raise ValueError("Invalid reference price")

    buy_price = reference_price - (BUY_GRID_FACTOR * GRID_STEP)
    sell_price = reference_price + (SELL_GRID_FACTOR * GRID_STEP)

    if buy_price <= 0:
        raise ValueError("Invalid buy price")

    size = round(BUDGET_USDC / reference_price, 5)
    return (
        {"side": "BUY", "price": buy_price, "size": size},
        {"side": "SELL", "price": sell_price, "size": size},
    )


def classify_order_result(result):
    """Purpose:
    Normalize an exchange order response into the module's status labels.

    Inputs:
    result: Mapping that may contain `response.data.statuses`.

    Outputs:
    Returns `"ok"` when the first status contains `resting` or `filled`, `"insufficient_spot_balance"` when the first status contains an error message with `Insufficient spot balance`, and `"error"` for missing statuses or all other status shapes. Logs failures before returning non-`"ok"` results.
    """
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        log_msg(f"order failed: {result}")
        return "error"

    status = statuses[0]
    if "error" in status:
        error = status["error"]
        log_msg(f"order failed: {error}")
        if "Insufficient spot balance" in error:
            return "insufficient_spot_balance"
        return "error"

    if "resting" in status or "filled" in status:
        return "ok"

    log_msg(f"order failed: {result}")
    return "error"


def place_limit_order(exchange, action):
    """Purpose:
    Submit one GTC limit order and classify the exchange response.

    Inputs:
    exchange: Hyperliquid `Exchange` client with `order`.
    action: Mapping with `side`, `size`, and `price`, where `side == "BUY"` selects the buy flag.

    Outputs:
    Returns the normalized status string from `classify_order_result()`. Propagates exceptions raised by `exchange.order()` or by required action field access and conversion.
    """
    result = exchange.order(
        SYMBOL,
        action["side"] == "BUY",
        float(action["size"]),
        float(action["price"]),
        {"limit": {"tif": "Gtc"}},
        False,
    )
    return classify_order_result(result)


def wait_no_open_orders(info, max_tries=10, interval_sec=1.0):
    """Purpose:
    Poll until the configured account has no open orders or the retry limit is reached.

    Inputs:
    info: Hyperliquid `Info` client with `open_orders`.
    max_tries: Maximum number of polling attempts.
    interval_sec: Sleep interval between polling attempts.

    Outputs:
    Returns `True` when an attempt observes no open orders; otherwise returns `False` after exhausting `max_tries`. Propagates exceptions raised by `get_open_orders()`.
    """
    for _ in range(max_tries):
        if not get_open_orders(info):
            return True
        time.sleep(interval_sec)
    return False


def cleanup_orders(info, exchange, orders):
    """Purpose:
    Cancel the provided open orders and verify that none remain.

    Inputs:
    info: Hyperliquid `Info` client used to confirm order removal.
    exchange: Hyperliquid `Exchange` client with `bulk_cancel`.
    orders: Iterable of order mappings with `oid`.

    Outputs:
    Returns `True` immediately when `orders` is empty, or after `bulk_cancel()` is followed by a successful `wait_no_open_orders()` check. Returns `False` and logs a cleanup-failure message when open orders remain after waiting. Propagates exceptions raised by `bulk_cancel()`, by order field access, or by `wait_no_open_orders()`.
    """
    if not orders:
        return True

    exchange.bulk_cancel([{"coin": SYMBOL, "oid": order["oid"]} for order in orders])
    if wait_no_open_orders(info):
        return True

    log_msg("cleanup failure: open orders still remain")
    return False


def place_pair(exchange, reference_price):
    """Purpose:
    Place the configured buy and sell orders for one grid rebuild attempt and derive the resulting mode state.

    Inputs:
    exchange: Hyperliquid `Exchange` client used to submit both orders.
    reference_price: Price anchor passed to `build_pair()`.

    Outputs:
    Returns a state mapping with `mode`, `buy_price`, `sell_price`, and `reference_price`. The mode is `PAIR` when both orders return `"ok"`, `BUY_ONLY` or `SELL_ONLY` on the configured insufficient-balance fallbacks, and `ABNORMAL` otherwise. Propagates exceptions raised by `build_pair()` or `place_limit_order()`.
    """
    buy_action, sell_action = build_pair(reference_price)
    log_msg(
        f"rebuilding: reference_price={reference_price} "
        f"buy={buy_action['price']} sell={sell_action['price']}"
    )
    buy_status = place_limit_order(exchange, buy_action)
    sell_status = place_limit_order(exchange, sell_action)

    if buy_status == "ok" and sell_status == "ok":
        return {
            "mode": PAIR_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    if (
        buy_status == "ok"
        and sell_status == "insufficient_spot_balance"
        and ALLOW_BUY_ONLY_WHEN_NO_BTC
    ):
        log_msg("buy-only fallback")
        return {
            "mode": BUY_ONLY_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    if (
        sell_status == "ok"
        and buy_status == "insufficient_spot_balance"
        and ALLOW_SELL_ONLY_WHEN_NO_USDC
    ):
        log_msg("sell-only fallback")
        return {
            "mode": SELL_ONLY_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    return {
        "mode": ABNORMAL_MODE,
        "buy_price": buy_action["price"],
        "sell_price": sell_action["price"],
        "reference_price": reference_price,
    }


def rebuild(info, exchange, orders, reference_price=None):
    """Purpose:
    Cancel existing orders, choose a reference price when needed, and attempt to establish a non-abnormal state.

    Inputs:
    info: Hyperliquid `Info` client used for order queries and optional mid-price lookup.
    exchange: Hyperliquid `Exchange` client used for cancellation and order placement.
    orders: Current open orders to cancel before rebuilding.
    reference_price: Optional explicit reference price; when `None`, `get_mid_reference_price()` is used.

    Outputs:
    Returns the state from `place_pair()` when it is not `ABNORMAL`. Returns `None` when cleanup fails before or after placement, or when the placement result is `ABNORMAL`. Propagates exceptions raised by cleanup, reference-price derivation, order queries, or order placement.
    """
    if orders and not cleanup_orders(info, exchange, orders):
        return None

    if reference_price is None:
        reference_price = get_mid_reference_price(info)

    state = place_pair(exchange, reference_price)
    if state is not None and state["mode"] != ABNORMAL_MODE:
        return state

    orders = get_open_orders(info)
    if orders and not cleanup_orders(info, exchange, orders):
        return None

    return None


def resolve_fill_rebuild_action(state, orders, order_shape):
    """Purpose:
    Resolve whether a fill event occurred and return corresponding rebuild action

    Inputs:
    state: Mapping with at least `mode`, `buy_price`, and `sell_price`.
    orders: Current open-order collection used for residual completion checks.
    order_shape: Current shape classification for `orders`.

    Outputs:
    Returns `("rebuild", reference_price)` for a prior `PAIR` state that becomes a single-sided residual, using the filled side's prior price as the rebuild reference. Returns `("rebuild", reference_price)` for `BUY_ONLY` or `SELL_ONLY` states when no orders remain, using that residual side's stored price. Returns `None` otherwise.
    """
    previous_mode = state["mode"]

    if previous_mode == PAIR_MODE:
        if order_shape == SELL_ONLY_MODE:
            log_msg("🔥 fill detected: BUY filled -> SELL residual -> rebuild")
            return "rebuild", state["buy_price"]

        if order_shape == BUY_ONLY_MODE:
            log_msg("✅ fill detected: SELL filled -> BUY residual -> rebuild")
            return "rebuild", state["sell_price"]

        return None

    if previous_mode == BUY_ONLY_MODE and len(orders) == 0:
        log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
        return "rebuild", state["buy_price"]

    if previous_mode == SELL_ONLY_MODE and len(orders) == 0:
        log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
        return "rebuild", state["sell_price"]

    return None


def validate_keep_state(info, orders, state, order_shape):
    """Purpose:
    Decide whether the current orders still satisfy the keep contract for a paired state.

    Inputs:
    info: Unused parameter preserved by the caller flow.
    orders: Current open orders.
    state: Stored state mapping with at least `mode`, `buy_price`, and `sell_price`.
    order_shape: Current shape classification for `orders`.

    Outputs:
    Returns `True` only for `PAIR` state. When `MANUAL_PAIR_TOLERANCE` is enabled, returns `True` if `is_manual_pair_keepable()` sees exactly one buy and one sell. Otherwise returns `True` only when `order_shape` is `PAIR`, `get_pair_state()` succeeds, and `pair_matches_state()` confirms both prices within tolerance. Returns `False` on all other paths. Propagates exceptions raised by pair validation helpers on malformed order fields.
    """
    if state["mode"] == PAIR_MODE:
        if MANUAL_PAIR_TOLERANCE:
            if is_manual_pair_keepable(orders):
                log_keep_state("pair", "contract: keep pair")
                return True
            return False

        if order_shape != PAIR_MODE:
            return False

        current_pair = get_pair_state(
            orders,
            GRID_STEP,
            BUY_GRID_FACTOR,
            SELL_GRID_FACTOR,
            PAIR_PRICE_TOLERANCE,
            PAIR_MODE,
        )
        if current_pair is None:
            return False
        if not pair_matches_state(current_pair, state, PAIR_PRICE_TOLERANCE):
            return False
        log_keep_state("pair", "XXX contract: keep pair")
        return True

    return False


def detect_anchor_break(info, orders, state, order_shape):
    """Purpose:
    Detect when a single-sided residual order has moved far enough from the BTC mid price to force rebuild.

    Inputs:
    info: Hyperliquid `Info` client used by the residual reanchor helper.
    orders: Current open orders.
    state: Stored state mapping with at least `mode`.
    order_shape: Current shape classification for `orders`.

    Outputs:
    Returns `("rebuild", None)` only when reanchoring is enabled, the stored mode is single-sided, the current shape matches that mode, exactly one order is present, and `should_reanchor_residual_order()` returns `True`. Returns `None` on all other paths. Propagates exceptions raised by the helper on malformed order fields after its guarded checks.
    """
    if not REANCHOR_BREAK:
        return None

    if state["mode"] not in (BUY_ONLY_MODE, SELL_ONLY_MODE):
        return None

    if order_shape != state["mode"]:
        return None

    if len(orders) != 1:
        return None

    if not should_reanchor_residual_order(
        info,
        orders,
        state["mode"],
        BUY_ONLY_MODE,
        SELL_ONLY_MODE,
        BTC_MID_KEY,
        GRID_STEP,
        REANCHOR_BREAK,
        REANCHOR_BREAK_STEPS,
    ):
        return None

    log_msg("🪝 contract: anchor break")
    return "rebuild", None


def detect_single_sided_action(info, orders, state, order_shape):
    """Purpose:
    Decide whether a single-sided state should be kept or rebuilt.

    Inputs:
    info: Hyperliquid `Info` client used for anchor-break evaluation.
    orders: Current open orders.
    state: Stored state mapping with at least `mode`.
    order_shape: Current shape classification for `orders`.

    Outputs:
    Returns `("keep", None)` for valid single-sided residuals that do not trigger anchor-break rebuilds. Returns the result of `detect_anchor_break()` when it requests rebuild. Returns `None` when the stored mode is not single-sided, when the current shape does not match the stored mode, or when the residual order side does not match the expected side.
    """
    if state["mode"] == BUY_ONLY_MODE:
        if order_shape != BUY_ONLY_MODE:
            return None
        if len(orders) != 1 or orders[0]["side"] != "B":
            return None

        anchor_break_action = detect_anchor_break(info, orders, state, order_shape)
        if anchor_break_action is not None:
            return anchor_break_action

        log_keep_state("buy-only", "contract: keep buy-only")
        return "keep", None

    if state["mode"] == SELL_ONLY_MODE:
        if order_shape != SELL_ONLY_MODE:
            return None
        if len(orders) != 1 or orders[0]["side"] == "B":
            return None

        anchor_break_action = detect_anchor_break(info, orders, state, order_shape)
        if anchor_break_action is not None:
            return anchor_break_action

        log_keep_state("sell-only", "contract: keep sell-only")
        return "keep", None

    return None


def get_loop_action(info, orders, state):
    """
    作用：
    根据当前 open orders 与上一轮已接受的 state，
    判定这一轮主循环应执行的唯一动作。

    判定顺序：
    1. 先分类当前订单形状
    2. 优先检查是否命中 fill-driven rebuild
    3. 再检查 PAIR_MODE 是否满足 keep 条件
    4. 再检查 BUY_ONLY / SELL_ONLY 是否应 keep 或触发 anchor break
    5. 若以上都不满足，则判定为 abnormal

    返回：
    - ("keep", None)
    - ("rebuild", reference_price 或 None)
    - ("abnormal", None)

    边界：
    本函数只负责决策，不负责下单、撤单或实际 rebuild 执行。
    """
    order_shape = classify_order_shape(
        orders,
        GRID_STEP,
        BUY_GRID_FACTOR,
        SELL_GRID_FACTOR,
        PAIR_PRICE_TOLERANCE,
        PAIR_MODE,
        BUY_ONLY_MODE,
        SELL_ONLY_MODE,
        ABNORMAL_MODE,
    )

    fill_driven_action = resolve_fill_rebuild_action(state, orders, order_shape)
    if fill_driven_action is not None:
        return fill_driven_action

    if validate_keep_state(info, orders, state, order_shape):
        return "keep", None

    single_sided_action = detect_single_sided_action(info, orders, state, order_shape)
    if single_sided_action is not None:
        return single_sided_action

    log_msg("contract: abnormal")
    return "abnormal", None


def run_main_loop(info, exchange, state):
    """Purpose:
    Poll open orders continuously and apply keep, rebuild, or abnormal handling until exit.

    Inputs:
    info: Hyperliquid `Info` client used for polling and rebuild decisions.
    exchange: Hyperliquid `Exchange` client used when rebuilds are required.
    state: Initial accepted state mapping for the running contract.

    Outputs:
    Returns `None`. Continues indefinitely while actions resolve to `keep` or successful `rebuild`; exits after a rebuild failure, an abnormal rebuild result, or `MAX_ABNORMAL_COUNT` consecutive abnormal loop actions. Logs abnormal summaries and exit conditions as they occur.
    """
    abnormal_count = 0

    while True:
        time.sleep(1.0)
        orders = get_open_orders(info)
        action, reference_price = get_loop_action(info, orders, state)

        if action == "keep":
            abnormal_count = 0
            continue

        if action == "rebuild":
            state = rebuild(info, exchange, orders, reference_price)
            if state is None or state["mode"] == ABNORMAL_MODE:
                log_msg("rebuild failed or abnormal mode, exit")
                return
            abnormal_count = 0
            continue

        abnormal_count += 1
        buy_count, sell_count = summarize_orders(orders)
        log_msg(
            f"abnormal state {abnormal_count}/{MAX_ABNORMAL_COUNT}: "
            f"buy={buy_count} sell={sell_count}"
        )
        if abnormal_count >= MAX_ABNORMAL_COUNT:
            log_msg("abnormal exit")
            return


def create_exchange():
    """Purpose:
    Construct the Hyperliquid exchange client for the configured API key and account address.

    Inputs:
    None.

    Outputs:
    Returns an `Exchange` instance bound to `constants.MAINNET_API_URL` and `ACCOUNT_ADDRESS`. Raises `ValueError` when `API_KEY` is missing. Propagates exceptions raised by `Account.from_key()` or `Exchange`.
    """
    if not API_KEY:
        raise ValueError("Missing HYPERLIQUID_API_KEY")

    return Exchange(
        Account.from_key(API_KEY),
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )


def main():
    """Purpose:
    Start the grid process from the current open orders and keep it running until exit conditions are met.

    Inputs:
    None.

    Outputs:
    Returns `None`. Logs and exits immediately when `ACCOUNT_ADDRESS` is missing. Otherwise builds clients, summarizes current orders, accepts an existing valid pair when present, falls back to `rebuild()` when needed, and enters `run_main_loop()` only when a non-abnormal initial state is established. Propagates exceptions from client construction, state derivation, and loop execution.
    """
    if not ACCOUNT_ADDRESS:
        log_msg("Missing HYPERLIQUID_ACCOUNT_ADDRESS")
        return

    info = Info(constants.MAINNET_API_URL)
    exchange = create_exchange()

    orders = get_open_orders(info)
    buy_count, sell_count = summarize_orders(orders)

    state = None
    if buy_count == 1 and sell_count == 1:
        state = get_pair_state(
            orders,
            GRID_STEP,
            BUY_GRID_FACTOR,
            SELL_GRID_FACTOR,
            PAIR_PRICE_TOLERANCE,
            PAIR_MODE,
        )
        if state is not None:
            log_msg("pair valid")

    if state is None:
        state = rebuild(info, exchange, orders)
        if state is None or state["mode"] == ABNORMAL_MODE:
            log_msg("initial rebuild failed or abnormal mode")
            return

    run_main_loop(info, exchange, state)


if __name__ == "__main__":
    main()
