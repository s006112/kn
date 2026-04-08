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
KEEP_LOG_INTERVAL_SEC = 60

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
    return info.open_orders(ACCOUNT_ADDRESS)


def log_msg(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def log_keep_state(keep_type, message):
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
    for _ in range(max_tries):
        if not get_open_orders(info):
            return True
        time.sleep(interval_sec)
    return False


def cleanup_orders(info, exchange, orders):
    if not orders:
        return True

    exchange.bulk_cancel([{"coin": SYMBOL, "oid": order["oid"]} for order in orders])
    if wait_no_open_orders(info):
        return True

    log_msg("cleanup failure: open orders still remain")
    return False


def place_pair(exchange, reference_price):
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


def detect_fill_driven_rebuild(state, orders, order_shape):
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
    if state["mode"] == PAIR_MODE:
        if MANUAL_PAIR_TOLERANCE:
            if is_manual_pair_keepable(orders):
                #log_keep_state("pair", "contract: keep pair")
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

        #log_keep_state("pair", "contract: keep pair")
        return True

    return False


def detect_anchor_break(info, orders, state, order_shape):
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

    fill_driven_action = detect_fill_driven_rebuild(state, orders, order_shape)
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
    if not API_KEY:
        raise ValueError("Missing HYPERLIQUID_API_KEY")

    return Exchange(
        Account.from_key(API_KEY),
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )


def main():
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