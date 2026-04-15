"""Engine shell and entrypoint for the REST polling grid."""

import time
import traceback


from grid_config import apply_runtime_overrides

apply_runtime_overrides({
    "GRID_STEP": 100.0,
    "BUDGET_USDC": 100.0,
    "BUY_GRID_FACTOR": 1.0,
    "SELL_GRID_FACTOR": 1.0
})

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
from grid_decision import get_bootstrap_live_state, get_loop_action
from grid_execution import rebuild
from grid_gateway import get_open_orders, read_btc_mid
from grid_config import (
    ACCOUNT_ADDRESS,
    API_KEY,
    BUY_ONLY_MODE,
    MAIN_LOOP_POLL_INTERVAL_SEC,
    PAIR_MODE,
    SELL_ONLY_MODE,
    log_msg,
    summarize_orders,
)


def create_exchange():
    if not API_KEY:
        raise ValueError("Missing HYPERLIQUID_API_KEY")

    return Exchange(
        Account.from_key(API_KEY),
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )


def log_exception_summary(stage, exc):
    log_msg(f"infra failure at {stage}: {type(exc).__name__}: {exc}")
    last_line = traceback.format_exc().strip().splitlines()[-1]
    if last_line:
        log_msg(f"exception summary: {last_line}")


def bootstrap_saved_state(info, exchange):
    orders = get_open_orders(info)
    summarize_orders(orders)

    state = get_bootstrap_live_state(orders)
    if state is not None:
        if state["mode"] == PAIR_MODE:
            log_msg("Bootstrap Pair")
        elif state["mode"] == BUY_ONLY_MODE:
            log_msg("Bootstrap Buy-only")
        elif state["mode"] == SELL_ONLY_MODE:
            log_msg("Bootstrap Sell-only")
        return state

    state = rebuild(info, exchange, orders)
    if state is None:
        log_msg("Bootstrap Rebuild Failed")
        return None

    return state


def step_engine(info, exchange, saved_state):
    orders = get_open_orders(info)
    btc_mid = read_btc_mid(info) if saved_state["mode"] == BUY_ONLY_MODE else None
    action, reference_price = get_loop_action(orders, saved_state, btc_mid)

    if action == "keep":
        return saved_state

    if action == "rebuild":
        new_state = rebuild(info, exchange, orders, reference_price)
        if new_state is None:
            log_msg("rebuild failed")
        return new_state

    summarize_orders(orders)
    log_msg("abnormal")
    return None


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

    try:
        saved_state = bootstrap_saved_state(info, exchange)
    except Exception as exc:
        log_exception_summary("Bootstrap", exc)
        log_msg("abnormal")
        return

    if saved_state is None:
        return

    while True:
        time.sleep(MAIN_LOOP_POLL_INTERVAL_SEC)
        try:
            saved_state = step_engine(info, exchange, saved_state)
        except Exception as exc:
            log_exception_summary("main-loop", exc)
            log_msg("abnormal")
            return

        if saved_state is None:
            return


if __name__ == "__main__":
    main()