"""Engine shell and entrypoint for the REST polling grid."""

import traceback
import time

from eth_account import Account

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from grid_config import (
    ABNORMAL_MODE,
    ACCOUNT_ADDRESS,
    API_KEY,
    BUY_ONLY_MODE,
    MAIN_LOOP_POLL_INTERVAL_SEC,
    PAIR_MODE,
    SELL_ONLY_MODE,
    log_msg,
    summarize_orders,
)
from grid_decision import get_bootstrap_live_state, get_loop_action
from grid_execution import rebuild
from grid_gateway import get_open_orders, read_current_btc_mid


def create_exchange():
    if not API_KEY:
        raise ValueError("Missing HYPERLIQUID_API_KEY")

    return Exchange(
        Account.from_key(API_KEY),
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )


def create_runtime_state(saved_state, live_orders):
    return {
        "saved_state": saved_state,
        "live_orders": list(live_orders),
        "live_btc_mid": None,
        "rebuild_in_flight": False,
    }


def refresh_runtime_inputs(info, runtime_state):
    saved_state = runtime_state["saved_state"]
    runtime_state["live_orders"] = get_open_orders(info)
    runtime_state["live_btc_mid"] = (
        read_current_btc_mid(info) if saved_state["mode"] == BUY_ONLY_MODE else None
    )


def bootstrap_saved_state(info, exchange):
    orders = get_open_orders(info)
    summarize_orders(orders)

    state = get_bootstrap_live_state(orders)
    if state is not None:
        if state["mode"] == PAIR_MODE:
            log_msg("bootstrap pair")
        elif state["mode"] == BUY_ONLY_MODE:
            log_msg("bootstrap buy-only")
        elif state["mode"] == SELL_ONLY_MODE:
            log_msg("bootstrap sell-only")
        return state, orders

    state = rebuild(info, exchange, orders)
    if state is None or state["mode"] == ABNORMAL_MODE:
        log_msg("bootstrap rebuild failed")
        return None, orders

    return state, orders


def execute_rebuild(info, exchange, runtime_state, reference_price):
    if runtime_state["rebuild_in_flight"]:
        return "keep"

    runtime_state["rebuild_in_flight"] = True
    try:
        new_state = rebuild(
            info,
            exchange,
            runtime_state["live_orders"],
            reference_price,
        )
    finally:
        runtime_state["rebuild_in_flight"] = False

    if new_state is None or new_state["mode"] == ABNORMAL_MODE:
        log_msg("rebuild failed")
        return "abnormal"

    runtime_state["saved_state"] = new_state
    return "rebuild"


def step_engine(info, exchange, runtime_state):
    refresh_runtime_inputs(info, runtime_state)

    orders = runtime_state["live_orders"]
    state = runtime_state["saved_state"]
    btc_mid = runtime_state["live_btc_mid"]

    action, reference_price = get_loop_action(orders, state, btc_mid)

    if action == "keep":
        return "keep"

    if action == "rebuild":
        return execute_rebuild(info, exchange, runtime_state, reference_price)

    summarize_orders(orders)
    log_msg("abnormal")
    return "abnormal"


def log_exception_summary(stage, exc):
    log_msg(f"infra failure at {stage}: {type(exc).__name__}: {exc}")
    last_line = traceback.format_exc().strip().splitlines()[-1]
    if last_line:
        log_msg(f"exception summary: {last_line}")


def safe_bootstrap_saved_state(info, exchange):
    try:
        return bootstrap_saved_state(info, exchange)
    except Exception as exc:
        log_exception_summary("bootstrap", exc)
        log_msg("abnormal")
        return None, []


def safe_step_engine(info, exchange, runtime_state):
    try:
        return step_engine(info, exchange, runtime_state)
    except Exception as exc:
        log_exception_summary("main-loop", exc)
        log_msg("abnormal")
        return "abnormal"


def run_main_loop(info, exchange, runtime_state):
    while True:
        time.sleep(MAIN_LOOP_POLL_INTERVAL_SEC)
        if safe_step_engine(info, exchange, runtime_state) == "abnormal":
            return


def main():
    if not ACCOUNT_ADDRESS:
        log_msg("Missing HYPERLIQUID_ACCOUNT_ADDRESS")
        return

    try:
        info = Info(constants.MAINNET_API_URL)
        exchange = create_exchange()
    except Exception as exc:
        log_exception_summary("startup", exc)
        log_msg("abnormal")
        return

    saved_state, live_orders = safe_bootstrap_saved_state(info, exchange)
    if saved_state is None:
        return

    runtime_state = create_runtime_state(saved_state, live_orders)
    run_main_loop(info, exchange, runtime_state)


if __name__ == "__main__":
    main()