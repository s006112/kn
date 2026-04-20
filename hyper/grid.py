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
from grid_decision import classify_order_mode, decide_cycle_action
from grid_execution import rebuild
from grid_config import read_orders, read_btc_mid
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
    info = Info(constants.MAINNET_API_URL)      # read SDK 内置的主网 API 地址常量
    trader = Exchange(                          # execution client for placing/canceling orders
        Account.from_key(API_WALLET_KEY),
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )

    orders = read_orders(info)
    summarize_orders(orders)

    live_state = classify_order_mode(orders)
    if live_state["mode"] == PAIR_MODE:
        log_msg("Bootstrap Pair")
        return live_state, info, trader

    state = rebuild(info, trader, orders)       # state structure: {"mode": PAIR_MODE, "buy_price": float, "sell_price": float}
    if state is None:
        log_msg("Bootstrap Rebuild Failed")
        return None, info, trader

    return info, trader, state


def run_cycle(info, trader, saved_state):
    orders = read_orders(info)

    btc_mid = None
    if saved_state["mode"] == BUY_ONLY_MODE:
        btc_mid = read_btc_mid(info)

    live_snapshot = {
        "orders": orders,
        "live_state": classify_order_mode(orders),
        "btc_mid": btc_mid,
    }

    action, rebuild_price = decide_cycle_action(live_snapshot, saved_state)

    if action == "keep":
        return saved_state

    if action == "rebuild":
        new_state = rebuild(info, trader, live_snapshot["orders"], rebuild_price)
        if new_state is None:
            log_msg("rebuild failed")
        return new_state

    summarize_orders(live_snapshot["orders"])
    log_msg("abnormal")
    return None


def main():
    info, trader, saved_state = bootstrap()
    if saved_state is None:
        return

    while True:
        time.sleep(MAIN_LOOP_POLL_INTERVAL_SEC)
        saved_state = run_cycle(info, trader, saved_state)
        if saved_state is None:
            return


if __name__ == "__main__":
    main()