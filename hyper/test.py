import time

from grid_config import (
    GRID_STEP,
    PAIR_MODE,
    BUY_ONLY_MODE,
    SELL_ONLY_MODE,
    REANCHOR_BREAK_STEPS,
    API_KEY,
)
from grid_decision import get_bootstrap_live_state, get_loop_action


def log_res(case_name, result, expected):
    is_match = (result == expected)
    icon = "✅" if is_match else "❌"
    print(f"{icon} {case_name:.<75} Result: {str(result):<20} | Expected: {str(expected)}")


def log_note(case_name, result, note):
    # NOTE / TBD / Known gap 统一视为 ❌
    print(f"❌ {case_name:.<75} Result: {str(result):<20} | Note: {note}")


def mode_of_bootstrap(orders):
    state = get_bootstrap_live_state(orders)
    return state["mode"] if state else None


def order(side, price, size=None):
    o = {"side": side, "price": float(price)}
    if size is not None:
        o["size"] = float(size)
    return o


def pair_orders(buy_price=99800.0, sell_price=100200.0, buy_size=None, sell_size=None):
    return [
        order("BUY", buy_price, buy_size),
        order("SELL", sell_price, sell_size),
    ]


def buy_only_orders(price=99800.0, size=None):
    return [order("BUY", price, size)]


def sell_only_orders(price=100200.0, size=None):
    return [order("SELL", price, size)]


def pair_state(buy_price=99800.0, sell_price=100200.0):
    return {
        "mode": PAIR_MODE,
        "buy_price": float(buy_price),
        "sell_price": float(sell_price),
    }


def buy_only_state(buy_price=99800.0):
    return {
        "mode": BUY_ONLY_MODE,
        "buy_price": float(buy_price),
    }


def sell_only_state(sell_price=100200.0):
    return {
        "mode": SELL_ONLY_MODE,
        "sell_price": float(sell_price),
    }


def run_bootstrap_eval():
    print(f"🚀 Bootstrap Evaluation (GRID_STEP={GRID_STEP})")

    o_valid = pair_orders()

    log_res("Bootstrap: Valid Pair", mode_of_bootstrap(o_valid), PAIR_MODE)
    log_res("Bootstrap: Reversed Pair", mode_of_bootstrap([o_valid[1], o_valid[0]]), PAIR_MODE)
    log_res("Bootstrap: Partial", mode_of_bootstrap(o_valid[:1]), None)
    log_res("Bootstrap: Empty", mode_of_bootstrap([]), None)
    log_res("Bootstrap: >2 Orders", mode_of_bootstrap(o_valid + [order("BUY", 99600.0)]), None)

    log_res("Bootstrap: Two BUY", mode_of_bootstrap([order("BUY", 99800.0), order("BUY", 99600.0)]), None)
    log_res("Bootstrap: Two SELL", mode_of_bootstrap([order("SELL", 100200.0), order("SELL", 100400.0)]), None)
    log_res("Bootstrap: buy>=sell", mode_of_bootstrap(pair_orders(100200.0, 99800.0)), None)

    log_res("Bootstrap: Correct Gap", mode_of_bootstrap(pair_orders(99800.0, 100200.0)), PAIR_MODE)
    log_res("Bootstrap: Wrong Gap", mode_of_bootstrap(pair_orders(99900.0, 100000.0)), None)

    log_note(
        "Bootstrap: Size Mismatch (Known Gap)",
        mode_of_bootstrap(pair_orders(99800.0, 100200.0, 1.0, 0.00001)),
        "no size validation",
    )

    log_res("Bootstrap: API Key", "Exists" if API_KEY else "Missing", "Exists")


def run_pair_mode_eval():
    print("\n🚀 PAIR Mode")

    o_pair = pair_orders()
    state_p = pair_state()

    log_res("PAIR: SELL filled", get_loop_action(o_pair[:1], state_p), ("rebuild", 100200.0))
    log_res("PAIR: BUY filled", get_loop_action(o_pair[1:], state_p), ("rebuild", 99800.0))
    log_res("PAIR: Keep", get_loop_action(o_pair, state_p), ("keep", None))

    log_res("PAIR: 0 orders", get_loop_action([], state_p), ("abnormal", None))
    log_res("PAIR: same side", get_loop_action([order("BUY", 99800.0), order("BUY", 99700.0)], state_p), ("abnormal", None))
    log_res("PAIR: drift", get_loop_action(pair_orders(99000.0, 100200.0), state_p), ("abnormal", None))
    log_res("PAIR: >2 orders", get_loop_action(o_pair + [order("BUY", 99600.0)], state_p), ("abnormal", None))


def run_buy_only_eval():
    print("\n🚀 BUY_ONLY Mode")

    state_b = buy_only_state(99800.0)
    live_buy = buy_only_orders(99800.0)

    log_res("BUY_ONLY: fill -> rebuild", get_loop_action([], state_b), ("rebuild", 99800.0))
    log_res("BUY_ONLY: keep", get_loop_action(live_buy, state_b), ("keep", None))

    log_note(
        "BUY_ONLY: price drift (Expected Negative)",
        get_loop_action(buy_only_orders(99700.0), state_b),
        "no price validation",
    )

    distance = REANCHOR_BREAK_STEPS * GRID_STEP
    log_res("BUY_ONLY: break < threshold", get_loop_action(live_buy, state_b, 99800.0 + distance - 1.0), ("keep", None))
    log_note("BUY_ONLY: break == threshold (TBD)", get_loop_action(live_buy, state_b, 99800.0 + distance), "TBD")
    log_res("BUY_ONLY: break > threshold", get_loop_action(live_buy, state_b, 99800.0 + distance + 1.0), ("rebuild", None))

    log_res("BUY_ONLY: mid None", get_loop_action(live_buy, state_b, None), ("keep", None))
    log_res("BUY_ONLY: mid <=0", get_loop_action(live_buy, state_b, 0.0), ("keep", None))

    log_res("BUY_ONLY: wrong side", get_loop_action(sell_only_orders(), state_b), ("abnormal", None))
    log_note("BUY_ONLY: pair snapshot (TBD)", get_loop_action(pair_orders(), state_b), "TBD")
    log_res("BUY_ONLY: multiple BUY", get_loop_action([order("BUY", 99800.0), order("BUY", 99700.0)], state_b), ("abnormal", None))


def run_sell_only_eval():
    print("\n🚀 SELL_ONLY Mode")

    state_s = sell_only_state(100200.0)
    live_sell = sell_only_orders(100200.0)

    log_res("SELL_ONLY: fill -> rebuild", get_loop_action([], state_s), ("rebuild", 100200.0))
    log_res("SELL_ONLY: keep", get_loop_action(live_sell, state_s), ("keep", None))

    log_note(
        "SELL_ONLY: price drift (Expected Negative)",
        get_loop_action(sell_only_orders(100300.0), state_s),
        "no price validation",
    )

    log_res("SELL_ONLY: wrong side", get_loop_action(buy_only_orders(), state_s), ("abnormal", None))
    log_note("SELL_ONLY: pair snapshot (TBD)", get_loop_action(pair_orders(), state_s), "TBD")
    log_res("SELL_ONLY: multiple SELL", get_loop_action([order("SELL", 100200.0), order("SELL", 100400.0)], state_s), ("abnormal", None))


if __name__ == "__main__":
    run_bootstrap_eval()
    run_pair_mode_eval()
    run_buy_only_eval()
    run_sell_only_eval()
    print("\n🏁 Test Cycle Complete.")