import time

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
    CONSECUTIVE_READS,
    MAIN_LOOP_POLL_INTERVAL_SEC,
    ORDER_ZONE,
    PAIR_MODE,
    BUY_ONLY_MODE,
    SELL_ONLY_MODE,
    TICK_COUNT,
    log_msg,
    summarize_orders,
)


def bootstrap():
    info = Info(constants.MAINNET_API_URL, skip_ws=True)     # read SDK 内置的主网 API 地址常量
    trader = Exchange(                          # execution client for placing/canceling orders
        Account.from_key(API_WALLET_KEY),
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )

    orders = read_orders(info)
    summarize_orders(orders)

    state = classify_order_mode(orders)

    # Important safety rule:
    # If a valid one-sided residual order already exists, keep it as-is.
    # Do NOT reset/reprice it around current market on restart.
    if state["mode"] in (PAIR_MODE, BUY_ONLY_MODE, SELL_ONLY_MODE):
        log_msg(f"Bootstrap {state['mode']}")
        return info, trader, state

    # Only rebuild from scratch when there are no open orders.
    # If open orders exist but do not match a known safe shape, halt for manual check.
    if orders:
        log_msg("Bootstrap abnormal open order layout; halt")
        return info, trader, None

    live_snapshot = {
        "orders": orders,
        "btc_mid": None,
        **state,
    }

    state = rebuild(info, trader, live_snapshot)  # state structure: {"mode": PAIR_MODE, "buy_price": float, "sell_price": float}
    if state is None:
        log_msg("Bootstrap Rebuild Failed")
        return info, trader, None

    return info, trader, state


def in_order_zone(btc_mid, saved_state):
    mode = saved_state.get("mode")

    if mode == PAIR_MODE:
        return (
            btc_mid <= saved_state["buy_price"] + ORDER_ZONE
            or btc_mid >= saved_state["sell_price"] - ORDER_ZONE
        )

    if mode == BUY_ONLY_MODE:
        return btc_mid <= saved_state["buy_price"] + ORDER_ZONE

    if mode == SELL_ONLY_MODE:
        return btc_mid >= saved_state["sell_price"] - ORDER_ZONE

    # Unknown state: force refresh instead of trusting stale state.
    return True


def run_cycle(info, trader, saved_state, tick_count, live_poll_interval_sec):
    btc_mid = read_btc_mid(info)
    log_msg(f"| {btc_mid} | {live_poll_interval_sec}")
    refresh_orders = False

    if btc_mid is None:
        refresh_orders = True
    elif tick_count % TICK_COUNT == 0:
        refresh_orders = True
    elif in_order_zone(btc_mid, saved_state):
        refresh_orders = True

    if not refresh_orders:
        return saved_state, btc_mid

    orders = read_orders(info)
    summarize_orders(orders)
    live_snapshot = {
        "orders": orders,
        "btc_mid": btc_mid,
        **classify_order_mode(orders),
    }

    action, strategy, rebuild_price = decide_cycle_action(live_snapshot, saved_state)

    if action == "keep":
        return saved_state, btc_mid

    if action == "rebuild":
        new_state = rebuild(info, trader, live_snapshot, strategy, rebuild_price)
        if new_state is None:
            log_msg("rebuild failed")
        return new_state, btc_mid

    summarize_orders(live_snapshot["orders"])
    log_msg("abnormal")
    return None, btc_mid


def main():
    info, trader, saved_state = bootstrap()
    if saved_state is None:
        return

    tick_count = 0
    live_poll_interval_sec = MAIN_LOOP_POLL_INTERVAL_SEC
    last_btc_mid = None
    same_btc_mid_reads = 0

    while True:
        time.sleep(live_poll_interval_sec)
        tick_count += 1
        cycle_state = saved_state
        saved_state, btc_mid = run_cycle(info, trader, saved_state, tick_count, live_poll_interval_sec)
        if saved_state is None:
            return

        if btc_mid is None:
            last_btc_mid = None
            same_btc_mid_reads = 0
            live_poll_interval_sec = MAIN_LOOP_POLL_INTERVAL_SEC
            continue

        if btc_mid == last_btc_mid:
            same_btc_mid_reads += 1
        else:
            last_btc_mid = btc_mid
            same_btc_mid_reads = 1

        live_poll_interval_sec = MAIN_LOOP_POLL_INTERVAL_SEC * ( 1 + (same_btc_mid_reads // CONSECUTIVE_READS) )
        if in_order_zone(btc_mid, cycle_state):
            live_poll_interval_sec /= 1

if __name__ == "__main__":
    main()
