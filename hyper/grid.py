import time

from grid_config import apply_runtime_overrides

apply_runtime_overrides({
    "GRID_STEP": 200.0,
    "BUDGET_USDC": 250.0,
    "BUY_GRID_FACTOR": 1.0,
    "SELL_GRID_FACTOR": 1.0,
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
    API_WALLET_KEY,
    BUY_ONLY_MODE,
    MAIN_LOOP_POLL_INTERVAL_SEC,
    PAIR_MODE,
    log_msg,
    summarize_orders,
)


def bootstrap():
    info = Info(constants.MAINNET_API_URL)
    trader = Exchange(
        Account.from_key(API_WALLET_KEY),
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )

    orders = get_open_orders(info)
    summarize_orders(orders)

    state = get_bootstrap_live_state(orders)
    if state is not None and state.get("mode") == PAIR_MODE:
        log_msg("Bootstrap Pair")
        return state, info, trader

    state = rebuild(info, trader, orders)
    if state is None:
        log_msg("Bootstrap Rebuild Failed")
        return None, info, trader

    return state, info, trader


def step_engine(info, trader, saved_state):
    orders = get_open_orders(info)
    btc_mid = read_btc_mid(info) if saved_state["mode"] == BUY_ONLY_MODE else None
    action, reference_price = get_loop_action(orders, saved_state, btc_mid)

    if action == "keep":
        return saved_state

    if action == "rebuild":
        new_state = rebuild(info, trader, orders, reference_price)
        if new_state is None:
            log_msg("rebuild failed")
        return new_state

    summarize_orders(orders)
    log_msg("abnormal")
    return None


def main():
    saved_state, info, trader = bootstrap()
    if saved_state is None:
        return

    while True:
        time.sleep(MAIN_LOOP_POLL_INTERVAL_SEC)
        saved_state = step_engine(info, trader, saved_state)
        if saved_state is None:
            return


if __name__ == "__main__":
    main()