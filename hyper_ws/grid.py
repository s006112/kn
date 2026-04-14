"""Engine shell and entrypoint for the Hyperliquid spot grid."""

import time
import traceback

from eth_account import Account

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from grid_decision import get_bootstrap_live_state, get_loop_action
from grid_execution import rebuild
from grid_gateway import BtcMidFeed, LiveOrdersFeed, get_open_orders, read_current_btc_mid
from grid_config import (
    ABNORMAL_MODE,
    ACCOUNT_ADDRESS,
    API_KEY,
    BUY_ONLY_MODE,
    MAIN_LOOP_POLL_INTERVAL_SEC,
    PAIR_MODE,
    POLLING_VERIFY_INTERVAL_SEC,
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


def create_runtime_state(
    saved_state,
    live_orders,
    live_orders_feed=None,
    btc_mid_feed=None,
):
    now = time.time()
    return {
        "saved_state": saved_state,
        "live_orders": list(live_orders),
        "live_orders_feed": live_orders_feed,
        "live_orders_source": "bootstrap",
        "live_orders_ts": now,
        "last_orders_verify_ts": now,
        "live_btc_mid": None,
        "btc_mid_feed": btc_mid_feed,
        "last_btc_mid_verify_ts": now,
        "last_input_ts": now,
        "rebuild_in_flight": False,
    }


def log_live_orders_source_if_changed(runtime_state, new_source):
    old_source = runtime_state.get("live_orders_source")
    if old_source != new_source:
        log_msg(f"input source: {new_source}")
    runtime_state["live_orders_source"] = new_source


def should_poll_verify(runtime_state, key):
    return (time.time() - runtime_state[key]) >= POLLING_VERIFY_INTERVAL_SEC


def read_preferred_snapshot(feed, poll_read_func, runtime_state, verify_ts_key):
    snapshot = feed.get_snapshot() if feed else None
    if snapshot is None:
        snapshot = poll_read_func()
        if feed is not None:
            feed.seed(snapshot)
        runtime_state[verify_ts_key] = time.time()
        return snapshot, "polling-fallback"

    if should_poll_verify(runtime_state, verify_ts_key):
        snapshot = poll_read_func()
        if feed is not None:
            feed.seed(snapshot)
        runtime_state[verify_ts_key] = time.time()
        return snapshot, "polling-verify"

    return snapshot, "ws-cache"


def refresh_runtime_inputs(info, runtime_state):
    saved_state = runtime_state["saved_state"]

    live_orders, live_orders_source = read_preferred_snapshot(
        runtime_state.get("live_orders_feed"),
        lambda: get_open_orders(info),
        runtime_state,
        "last_orders_verify_ts",
    )
    log_live_orders_source_if_changed(runtime_state, live_orders_source)
    runtime_state["live_orders"] = live_orders
    runtime_state["live_orders_ts"] = time.time()

    if saved_state["mode"] == BUY_ONLY_MODE:
        btc_mid, _ = read_preferred_snapshot(
            runtime_state.get("btc_mid_feed"),
            lambda: read_current_btc_mid(info),
            runtime_state,
            "last_btc_mid_verify_ts",
        )
        runtime_state["live_btc_mid"] = btc_mid
    else:
        runtime_state["live_btc_mid"] = None

    runtime_state["last_input_ts"] = time.time()


def bootstrap_saved_state(info, exchange):
    orders = get_open_orders(info)
    summarize_orders(orders)

    state = get_bootstrap_live_state(orders)
    if state is not None and state["mode"] == PAIR_MODE:
        log_msg("pair valid")
        return state, orders

    if state is not None:
        log_msg("bootstrap single-sided residual detected, rebuild required")

    state = rebuild(info, exchange, orders)
    if state is None or state["mode"] == ABNORMAL_MODE:
        log_msg("initial rebuild failed or abnormal mode")
        return None, orders

    orders = get_open_orders(info)
    summarize_orders(orders)
    return state, orders


def execute_rebuild(info, exchange, runtime_state, reference_price):
    if runtime_state["rebuild_in_flight"]:
        log_msg("rebuild skipped: rebuild already in flight")
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
        log_msg("rebuild failed or abnormal mode, exit")
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

    buy_count, sell_count = summarize_orders(orders)
    log_msg(
        f"abnormal state: buy={buy_count} sell={sell_count} "
        f"source={runtime_state.get('live_orders_source')}"
    )
    log_msg("abnormal exit")
    return "abnormal"


def log_exception_summary(stage, exc):
    log_msg(f"abnormal exception at {stage}: {type(exc).__name__}: {exc}")
    last_line = traceback.format_exc().strip().splitlines()[-1]
    if last_line:
        log_msg(f"exception summary: {last_line}")


def safe_bootstrap_saved_state(info, exchange):
    try:
        return bootstrap_saved_state(info, exchange)
    except Exception as exc:
        log_exception_summary("bootstrap", exc)
        log_msg("abnormal exit")
        return None, []


def safe_step_engine(info, exchange, runtime_state):
    try:
        return step_engine(info, exchange, runtime_state)
    except Exception as exc:
        log_exception_summary("main-loop", exc)
        log_msg("abnormal exit")
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
        log_msg("abnormal exit")
        return

    saved_state, live_orders = safe_bootstrap_saved_state(info, exchange)
    if saved_state is None:
        return

    live_orders_feed = LiveOrdersFeed(info)
    live_orders_feed.seed(live_orders)
    live_orders_feed.start()

    btc_mid_feed = BtcMidFeed(info)
    btc_mid_feed.start()

    runtime_state = create_runtime_state(
        saved_state,
        live_orders,
        live_orders_feed,
        btc_mid_feed,
    )
    run_main_loop(info, exchange, runtime_state)


if __name__ == "__main__":
    main()