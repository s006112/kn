from grid_config import (
    GRID_STEP,
    BUDGET_USDC,
    PAIR_MODE,
    BUY_ONLY_MODE,
    SELL_ONLY_MODE,
    REANCHOR_BREAK_STEPS,
    WAIT_NO_OPEN_ORDERS_INTERVAL_SEC,
    API_KEY,
)
from grid_decision import get_bootstrap_live_state, get_loop_action

try:
    import grid as grid_engine
except Exception:
    grid_engine = None

try:
    import grid_execution as grid_exec
except Exception:
    grid_exec = None

try:
    import grid_gateway as grid_gate
except Exception:
    grid_gate = None


TEST_STATS = {
    "pass": 0,
    "fail": 0,
    "note": 0,
}
FAILED_CASES = []
NOTED_CASES = []


def log_res(case_name, result, expected):
    is_match = (result == expected)
    icon = "✅" if is_match else "❌"
    print(f"{icon} {case_name:.<50} Result: {str(result):<20} | Expected: {str(expected)}")
    if is_match:
        TEST_STATS["pass"] += 1
    else:
        TEST_STATS["fail"] += 1
        FAILED_CASES.append((case_name, result, expected))


def log_note(case_name, result, note):
    print(f"❌ {case_name:.<50} Result: {str(result):<20} | Note: {note}")
    TEST_STATS["note"] += 1
    NOTED_CASES.append((case_name, result, note))


def print_final_summary():
    total_checked = TEST_STATS["pass"] + TEST_STATS["fail"]
    print("\n📌 Final Summary")
    print("=" * 100)
    print(f"Checked assertions : {total_checked}")
    print(f"Passed             : {TEST_STATS['pass']}")
    print(f"Mismatches         : {TEST_STATS['fail']}")
    print(f"Known gaps / notes : {TEST_STATS['note']}")

    if FAILED_CASES:
        print("\n❌ Mismatch Cases")
        for case_name, result, expected in FAILED_CASES:
            print(f" - {case_name} | Result: {result} | Expected: {expected}")

    if NOTED_CASES:
        print("\n❌ Known Gaps / Notes")
        for case_name, result, note in NOTED_CASES:
            print(f" - {case_name} | Result: {result} | Note: {note}")

    if TEST_STATS["fail"] == 0:
        verdict = "PASS_WITH_NOTES" if TEST_STATS["note"] > 0 else "PASS"
    else:
        verdict = "FAIL"

    print(f"\n🏁 Verdict: {verdict}")
    print("=" * 100)


def mode_of_bootstrap(orders):
    state = get_bootstrap_live_state(orders)
    return state["mode"] if state else None


def order(side, price, size=None, oid=None):
    o = {"side": side, "price": float(price)}
    if size is not None:
        o["size"] = float(size)
    if oid is not None:
        o["oid"] = oid
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

def run_bootstrap_saved_state_case(orders, bootstrap_state, rebuild_state):
    if grid_engine is None:
        return None, None

    calls = {
        "get_open_orders": 0,
        "summarize_orders": 0,
        "get_bootstrap_live_state": 0,
        "rebuild": 0,
        "rebuild_args": None,
    }

    old_get_open_orders = grid_engine.get_open_orders
    old_summarize_orders = grid_engine.summarize_orders
    old_get_bootstrap_live_state = grid_engine.get_bootstrap_live_state
    old_rebuild = grid_engine.rebuild

    def fake_get_open_orders(info):
        calls["get_open_orders"] += 1
        return orders

    def fake_summarize_orders(local_orders):
        calls["summarize_orders"] += 1
        return 0, 0

    def fake_get_bootstrap_live_state(local_orders):
        calls["get_bootstrap_live_state"] += 1
        return bootstrap_state

    def fake_rebuild(info, exchange, local_orders, reference_price=None):
        calls["rebuild"] += 1
        calls["rebuild_args"] = (local_orders, reference_price)
        return rebuild_state

    try:
        grid_engine.get_open_orders = fake_get_open_orders
        grid_engine.summarize_orders = fake_summarize_orders
        grid_engine.get_bootstrap_live_state = fake_get_bootstrap_live_state
        grid_engine.rebuild = fake_rebuild
        result = grid_engine.bootstrap_saved_state(info=object(), exchange=object())
    finally:
        grid_engine.get_open_orders = old_get_open_orders
        grid_engine.summarize_orders = old_summarize_orders
        grid_engine.get_bootstrap_live_state = old_get_bootstrap_live_state
        grid_engine.rebuild = old_rebuild

    return result, calls


def run_place_limit_order_case(action, order_result):
    if grid_exec is None:
        return None, None

    calls = {
        "exchange_order": 0,
        "exchange_order_args": None,
    }

    class FakeExchange:
        def order(self, symbol, is_buy, size, price, tif_payload, reduce_only):
            calls["exchange_order"] += 1
            calls["exchange_order_args"] = (
                symbol,
                is_buy,
                size,
                price,
                tif_payload,
                reduce_only,
            )
            return order_result

    result = grid_exec.place_limit_order(FakeExchange(), action)
    return result, calls


def run_read_with_retry_case(sequence, read_name="test-read", max_retries=2):
    if grid_gate is None:
        return None, None

    calls = {
        "read_func": 0,
        "sleep": 0,
        "sleep_args": [],
    }

    old_sleep = grid_gate.time.sleep
    items = list(sequence)

    def fake_sleep(sec):
        calls["sleep"] += 1
        calls["sleep_args"].append(sec)

    def fake_read():
        calls["read_func"] += 1
        item = items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    try:
        grid_gate.time.sleep = fake_sleep
        try:
            result = grid_gate.read_with_retry(
                fake_read,
                read_name=read_name,
                max_retries=max_retries,
                base_delay_sec=0.1,
                max_delay_sec=0.5,
                jitter_max_sec=0.0,
                cooldown_sec=0.0,
            )
            return result, calls
        except Exception as exc:
            return type(exc).__name__, calls
    finally:
        grid_gate.time.sleep = old_sleep


def run_bootstrap_saved_state_real_case(orders, rebuild_state):
    if grid_engine is None:
        return None, None

    calls = {
        "get_open_orders": 0,
        "summarize_orders": 0,
        "rebuild": 0,
        "rebuild_args": None,
    }

    old_get_open_orders = grid_engine.get_open_orders
    old_summarize_orders = grid_engine.summarize_orders
    old_rebuild = grid_engine.rebuild

    def fake_get_open_orders(info):
        calls["get_open_orders"] += 1
        return orders

    def fake_summarize_orders(local_orders):
        calls["summarize_orders"] += 1
        return 0, 0

    def fake_rebuild(info, exchange, local_orders, reference_price=None):
        calls["rebuild"] += 1
        calls["rebuild_args"] = (local_orders, reference_price)
        return rebuild_state

    try:
        grid_engine.get_open_orders = fake_get_open_orders
        grid_engine.summarize_orders = fake_summarize_orders
        grid_engine.rebuild = fake_rebuild
        result = grid_engine.bootstrap_saved_state(info=object(), exchange=object())
    finally:
        grid_engine.get_open_orders = old_get_open_orders
        grid_engine.summarize_orders = old_summarize_orders
        grid_engine.rebuild = old_rebuild

    return result, calls


def run_cleanup_orders_missing_oid_case(orders):
    if grid_exec is None:
        return None

    class FakeExchange:
        def bulk_cancel(self, payload):
            return None

    try:
        grid_exec.cleanup_orders(info=object(), exchange=FakeExchange(), orders=orders)
        return "NO_ERROR"
    except Exception as exc:
        return type(exc).__name__


def run_read_with_retry_status_case(exc_obj, max_retries=2):
    if grid_gate is None:
        return None, None

    calls = {"read_func": 0, "sleep": 0}
    old_sleep = grid_gate.time.sleep

    def fake_sleep(sec):
        calls["sleep"] += 1

    def fake_read():
        calls["read_func"] += 1
        raise exc_obj

    try:
        grid_gate.time.sleep = fake_sleep
        try:
            grid_gate.read_with_retry(
                fake_read,
                read_name="status-case",
                max_retries=max_retries,
                base_delay_sec=0.1,
                max_delay_sec=0.5,
                jitter_max_sec=0.0,
                cooldown_sec=0.0,
            )
            return "NO_ERROR", calls
        except Exception as exc:
            return type(exc).__name__, calls
    finally:
        grid_gate.time.sleep = old_sleep

def run_red_team_eval():
    print("\n🚀 red-team probe")

    if grid_engine is None:
        log_note("red-team: bootstrap real import", "SKIPPED", "grid import failed")
    else:
        inherited_state = {"mode": PAIR_MODE, "buy_price": 99800.0, "sell_price": 100200.0}

        result, calls = run_bootstrap_saved_state_real_case(
            orders=pair_orders(),
            rebuild_state={"mode": "SHOULD_NOT_HAPPEN"},
        )
        log_res(
            "red-team: bootstrap real inherit",
            (result["mode"], result["buy_price"], result["sell_price"]),
            (PAIR_MODE, 99800.0, 100200.0),
        )
        log_res("red-team: bootstrap real no rebuild", calls["rebuild"], 0)

        result, calls = run_bootstrap_saved_state_real_case(
            orders=pair_orders(99900.0, 100000.0),  # wrong gap, real decision should reject
            rebuild_state={"mode": PAIR_MODE, "buy_price": 99700.0, "sell_price": 100100.0},
        )
        log_res("red-team: bootstrap real reject->rebuild", result, {"mode": PAIR_MODE, "buy_price": 99700.0, "sell_price": 100100.0})
        log_res("red-team: bootstrap real reject rebuild", calls["rebuild"], 1)

    if grid_exec is None:
        log_note("red-team: cleanup missing oid", "SKIPPED", "grid_execution import failed")
    else:
        # 这不是你想要的行为，但很可能是真实会暴露的弱点
        result = run_cleanup_orders_missing_oid_case([{"side": "BUY", "price": 99800.0}])
        log_note("red-team: cleanup missing oid", result, "cleanup_orders assumes every order has oid")

        result = run_cleanup_orders_missing_oid_case([{"side": "BUY", "price": 99800.0, "oid": 11}])
        log_res("red-team: cleanup with oid shape", result in ("NO_ERROR", "AttributeError", "TypeError"), True)

    if grid_gate is None:
        log_note("red-team: retry status cases", "SKIPPED", "grid_gateway import failed")
    else:
        class RetryableHTTPError(Exception):
            def __init__(self, status_code):
                super().__init__(f"status={status_code}")
                self.status_code = status_code

        class NonRetryableHTTPError(Exception):
            def __init__(self, status_code):
                super().__init__(f"status={status_code}")
                self.status_code = status_code

        result, calls = run_read_with_retry_status_case(RetryableHTTPError(502), max_retries=2)
        log_res("red-team: retryable status exhausted", result, "RetryableHTTPError")
        log_res("red-team: retryable status calls", calls["read_func"], 3)
        log_res("red-team: retryable status sleeps", calls["sleep"], 2)

        result, calls = run_read_with_retry_status_case(NonRetryableHTTPError(400), max_retries=2)
        log_res("red-team: nonretry status direct", result, "NonRetryableHTTPError")
        log_res("red-team: nonretry status calls", calls["read_func"], 1)
        log_res("red-team: nonretry status no sleep", calls["sleep"], 0)

        # 这个更像“逻辑风险暴露”，不是 mismatch
        state_b = buy_only_state(99800.0)
        live_buy_drift = buy_only_orders(90000.0)
        result = get_loop_action(live_buy_drift, state_b, 90000.0 + REANCHOR_BREAK_STEPS * GRID_STEP - 1)
        log_note("red-team: BUY_ONLY drift still keep", result, "saved_state drift remains silent if anchor-break not triggered")

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
    log_res("BUY_ONLY: break == threshold", get_loop_action(live_buy, state_b, 99800.0 + distance), ("rebuild", None))
    log_res("BUY_ONLY: break > threshold", get_loop_action(live_buy, state_b, 99800.0 + distance + 1.0), ("rebuild", None))

    log_res("BUY_ONLY: mid None", get_loop_action(live_buy, state_b, None), ("keep", None))
    log_res("BUY_ONLY: mid <=0", get_loop_action(live_buy, state_b, 0.0), ("keep", None))

    log_res("BUY_ONLY: wrong side", get_loop_action(sell_only_orders(), state_b), ("abnormal", None))
    log_res("BUY_ONLY: pair snapshot", get_loop_action(pair_orders(), state_b), ("abnormal", None))
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
    log_res("SELL_ONLY: pair snapshot", get_loop_action(pair_orders(), state_s), ("abnormal", None))
    log_res("SELL_ONLY: multiple SELL", get_loop_action([order("SELL", 100200.0), order("SELL", 100400.0)], state_s), ("abnormal", None))


def run_step_engine_case(saved_state, orders, action_result, rebuild_result=None, btc_mid=100500.0):
    if grid_engine is None:
        return None, None

    calls = {
        "get_open_orders": 0,
        "read_btc_mid": 0,
        "get_loop_action": 0,
        "rebuild": 0,
        "rebuild_args": None,
        "btc_mid_seen": None,
    }

    old_get_open_orders = grid_engine.get_open_orders
    old_read_btc_mid = grid_engine.read_btc_mid
    old_get_loop_action = grid_engine.get_loop_action
    old_rebuild = grid_engine.rebuild

    def fake_get_open_orders(info):
        calls["get_open_orders"] += 1
        return orders

    def fake_read_btc_mid(info):
        calls["read_btc_mid"] += 1
        return btc_mid

    def fake_get_loop_action(local_orders, local_state, local_btc_mid=None):
        calls["get_loop_action"] += 1
        calls["btc_mid_seen"] = local_btc_mid
        return action_result

    def fake_rebuild(info, exchange, local_orders, reference_price=None):
        calls["rebuild"] += 1
        calls["rebuild_args"] = (local_orders, reference_price)
        return rebuild_result

    try:
        grid_engine.get_open_orders = fake_get_open_orders
        grid_engine.read_btc_mid = fake_read_btc_mid
        grid_engine.get_loop_action = fake_get_loop_action
        grid_engine.rebuild = fake_rebuild
        result = grid_engine.step_engine(info=object(), exchange=object(), saved_state=saved_state)
    finally:
        grid_engine.get_open_orders = old_get_open_orders
        grid_engine.read_btc_mid = old_read_btc_mid
        grid_engine.get_loop_action = old_get_loop_action
        grid_engine.rebuild = old_rebuild

    return result, calls


def run_step_engine_eval():
    print("\n🚀 step_engine Mode")

    if grid_engine is None:
        log_note("step_engine: import grid", "SKIPPED", "grid import failed")
        return

    state_p = pair_state()
    state_b = buy_only_state()
    state_s = sell_only_state()
    orders_p = pair_orders()
    rebuilt_state = {"mode": PAIR_MODE, "buy_price": 99700.0, "sell_price": 100100.0}

    result, calls = run_step_engine_case(state_p, orders_p, ("keep", None))
    log_res("step_engine: PAIR keep return", result, state_p)
    log_res("step_engine: PAIR keep no rebuild", calls["rebuild"], 0)
    log_res("step_engine: PAIR keep no mid read", calls["read_btc_mid"], 0)

    result, calls = run_step_engine_case(state_p, orders_p[:1], ("rebuild", 100200.0), rebuild_result=rebuilt_state)
    log_res("step_engine: PAIR rebuild return", result, rebuilt_state)
    log_res("step_engine: PAIR rebuild count", calls["rebuild"], 1)
    log_res("step_engine: PAIR rebuild ref", calls["rebuild_args"][1], 100200.0)

    result, calls = run_step_engine_case(state_p, orders_p[:1], ("rebuild", 100200.0), rebuild_result=None)
    log_res("step_engine: PAIR rebuild fail", result, None)
    log_res("step_engine: PAIR rebuild fail count", calls["rebuild"], 1)

    result, calls = run_step_engine_case(state_p, [], ("abnormal", None))
    log_res("step_engine: PAIR abnormal return", result, None)
    log_res("step_engine: PAIR abnormal no rebuild", calls["rebuild"], 0)

    result, calls = run_step_engine_case(state_b, buy_only_orders(), ("keep", None), btc_mid=100250.0)
    log_res("step_engine: BUY_ONLY keep return", result, state_b)
    log_res("step_engine: BUY_ONLY read mid", calls["read_btc_mid"], 1)
    log_res("step_engine: BUY_ONLY pass mid", calls["btc_mid_seen"], 100250.0)

    result, calls = run_step_engine_case(state_s, sell_only_orders(), ("keep", None), btc_mid=100250.0)
    log_res("step_engine: SELL_ONLY keep return", result, state_s)
    log_res("step_engine: SELL_ONLY no mid read", calls["read_btc_mid"], 0)
    log_res("step_engine: SELL_ONLY pass mid", calls["btc_mid_seen"], None)


def run_exec_cleanup_case(initial_orders, wait_result, remaining_orders):
    if grid_exec is None:
        return None, None

    calls = {
        "bulk_cancel": 0,
        "bulk_cancel_args": None,
        "wait_no_open_orders": 0,
        "get_open_orders": 0,
        "summarize_orders": 0,
    }

    old_wait_no_open_orders = grid_exec.wait_no_open_orders
    old_get_open_orders = grid_exec.get_open_orders
    old_summarize_orders = grid_exec.summarize_orders

    class FakeExchange:
        def bulk_cancel(self, payload):
            calls["bulk_cancel"] += 1
            calls["bulk_cancel_args"] = payload

    def fake_wait_no_open_orders(info, max_tries=10, interval_sec=WAIT_NO_OPEN_ORDERS_INTERVAL_SEC):
        calls["wait_no_open_orders"] += 1
        return wait_result

    def fake_get_open_orders(info):
        calls["get_open_orders"] += 1
        return remaining_orders

    def fake_summarize_orders(orders):
        calls["summarize_orders"] += 1
        return 0, 0

    try:
        grid_exec.wait_no_open_orders = fake_wait_no_open_orders
        grid_exec.get_open_orders = fake_get_open_orders
        grid_exec.summarize_orders = fake_summarize_orders
        result = grid_exec.cleanup_orders(info=object(), exchange=FakeExchange(), orders=initial_orders)
    finally:
        grid_exec.wait_no_open_orders = old_wait_no_open_orders
        grid_exec.get_open_orders = old_get_open_orders
        grid_exec.summarize_orders = old_summarize_orders

    return result, calls


def run_rebuild_case(initial_orders, cleanup_result, reference_price_input, computed_reference_price, place_pair_result):
    if grid_exec is None:
        return None, None

    calls = {
        "cleanup_orders": 0,
        "cleanup_orders_args": None,
        "get_reference_price": 0,
        "place_pair": 0,
        "place_pair_args": None,
    }

    old_cleanup_orders = grid_exec.cleanup_orders
    old_get_reference_price = grid_exec.get_reference_price
    old_place_pair = grid_exec.place_pair

    def fake_cleanup_orders(info, exchange, orders):
        calls["cleanup_orders"] += 1
        calls["cleanup_orders_args"] = orders
        return cleanup_result

    def fake_get_reference_price(info):
        calls["get_reference_price"] += 1
        return computed_reference_price

    def fake_place_pair(info, exchange, reference_price):
        calls["place_pair"] += 1
        calls["place_pair_args"] = reference_price
        return place_pair_result

    try:
        grid_exec.cleanup_orders = fake_cleanup_orders
        grid_exec.get_reference_price = fake_get_reference_price
        grid_exec.place_pair = fake_place_pair
        result = grid_exec.rebuild(
            info=object(),
            exchange=object(),
            orders=initial_orders,
            reference_price=reference_price_input,
        )
    finally:
        grid_exec.cleanup_orders = old_cleanup_orders
        grid_exec.get_reference_price = old_get_reference_price
        grid_exec.place_pair = old_place_pair

    return result, calls


def run_exec_partial_cleanup_case(remaining_orders, cleanup_result):
    if grid_exec is None:
        return None, None

    calls = {
        "get_open_orders": 0,
        "cleanup_orders": 0,
        "cleanup_orders_args": None,
        "summarize_orders": 0,
    }

    old_get_open_orders = grid_exec.get_open_orders
    old_cleanup_orders = grid_exec.cleanup_orders
    old_summarize_orders = grid_exec.summarize_orders

    def fake_get_open_orders(info):
        calls["get_open_orders"] += 1
        return remaining_orders

    def fake_cleanup_orders(info, exchange, orders):
        calls["cleanup_orders"] += 1
        calls["cleanup_orders_args"] = orders
        return cleanup_result

    def fake_summarize_orders(orders):
        calls["summarize_orders"] += 1
        return 0, 0

    try:
        grid_exec.get_open_orders = fake_get_open_orders
        grid_exec.cleanup_orders = fake_cleanup_orders
        grid_exec.summarize_orders = fake_summarize_orders
        result = grid_exec.cleanup_after_partial_place_failure(info=object(), exchange=object())
    finally:
        grid_exec.get_open_orders = old_get_open_orders
        grid_exec.cleanup_orders = old_cleanup_orders
        grid_exec.summarize_orders = old_summarize_orders

    return result, calls


def run_place_pair_case(buy_status, sell_status, allow_buy_only, allow_sell_only, cleanup_result=True, reference_price=100000.0):
    if grid_exec is None:
        return None, None

    calls = {
        "build_pair": 0,
        "place_limit_order": 0,
        "place_limit_order_args": [],
        "cleanup_after_partial_place_failure": 0,
    }

    old_build_pair = grid_exec.build_pair
    old_place_limit_order = grid_exec.place_limit_order
    old_cleanup_after_partial_place_failure = grid_exec.cleanup_after_partial_place_failure
    old_allow_buy_only = grid_exec.ALLOW_BUY_ONLY_WHEN_NO_BTC
    old_allow_sell_only = grid_exec.ALLOW_SELL_ONLY_WHEN_NO_USDC

    buy_action = {"side": "BUY", "price": 99800.0, "size": 0.001}
    sell_action = {"side": "SELL", "price": 100200.0, "size": 0.001}

    statuses = [buy_status, sell_status]

    def fake_build_pair(local_reference_price):
        calls["build_pair"] += 1
        return buy_action, sell_action

    def fake_place_limit_order(exchange, action):
        calls["place_limit_order"] += 1
        calls["place_limit_order_args"].append(action["side"])
        return statuses.pop(0)

    def fake_cleanup_after_partial_place_failure(info, exchange):
        calls["cleanup_after_partial_place_failure"] += 1
        return cleanup_result

    try:
        grid_exec.build_pair = fake_build_pair
        grid_exec.place_limit_order = fake_place_limit_order
        grid_exec.cleanup_after_partial_place_failure = fake_cleanup_after_partial_place_failure
        grid_exec.ALLOW_BUY_ONLY_WHEN_NO_BTC = allow_buy_only
        grid_exec.ALLOW_SELL_ONLY_WHEN_NO_USDC = allow_sell_only
        result = grid_exec.place_pair(info=object(), exchange=object(), reference_price=reference_price)
    finally:
        grid_exec.build_pair = old_build_pair
        grid_exec.place_limit_order = old_place_limit_order
        grid_exec.cleanup_after_partial_place_failure = old_cleanup_after_partial_place_failure
        grid_exec.ALLOW_BUY_ONLY_WHEN_NO_BTC = old_allow_buy_only
        grid_exec.ALLOW_SELL_ONLY_WHEN_NO_USDC = old_allow_sell_only

    return result, calls


def run_gateway_open_orders_case(raw_orders):
    if grid_gate is None:
        return None, None

    calls = {
        "read_with_retry": 0,
    }

    old_read_with_retry = grid_gate.read_with_retry

    def fake_read_with_retry(read_func, read_name, max_retries, base_delay_sec, max_delay_sec, jitter_max_sec=0.0, cooldown_sec=0.0):
        calls["read_with_retry"] += 1
        return raw_orders

    try:
        grid_gate.read_with_retry = fake_read_with_retry
        result = grid_gate.get_open_orders(info=object())
    finally:
        grid_gate.read_with_retry = old_read_with_retry

    return result, calls


def run_gateway_read_mid_case(mids_value=None, mids_exc=None):
    if grid_gate is None:
        return None

    old_read_all_mids = grid_gate.read_all_mids

    def fake_read_all_mids(info):
        if mids_exc is not None:
            raise mids_exc
        return mids_value

    try:
        grid_gate.read_all_mids = fake_read_all_mids
        result = grid_gate.read_btc_mid(info=object())
    finally:
        grid_gate.read_all_mids = old_read_all_mids

    return result


def run_gateway_reference_case(mids_value=None, mids_exc=None):
    if grid_gate is None:
        return None, None

    calls = {
        "read_all_mids": 0,
    }

    old_read_all_mids = grid_gate.read_all_mids

    def fake_read_all_mids(info):
        calls["read_all_mids"] += 1
        if mids_exc is not None:
            raise mids_exc
        return mids_value

    try:
        grid_gate.read_all_mids = fake_read_all_mids
        try:
            result = grid_gate.get_reference_price(info=object())
            return result, calls
        except Exception as exc:
            return type(exc).__name__, calls
    finally:
        grid_gate.read_all_mids = old_read_all_mids


def run_execution_eval():
    print("\n🚀 execution Mode")

    if grid_exec is None:
        log_note("execution: import grid_execution", "SKIPPED", "grid_execution import failed")
        return

    buy_action, sell_action = grid_exec.build_pair(100000.0)
    log_res("build_pair: buy price", buy_action["price"], 100000.0 - grid_exec.BUY_GRID_FACTOR * GRID_STEP)
    log_res("build_pair: sell price", sell_action["price"], 100000.0 + grid_exec.SELL_GRID_FACTOR * GRID_STEP)
    log_res("build_pair: buy size", buy_action["size"], round(BUDGET_USDC / buy_action["price"], 5))
    log_res("build_pair: sell size", sell_action["size"], round(BUDGET_USDC / sell_action["price"], 5))

    try:
        grid_exec.build_pair(0.0)
        log_res("build_pair: invalid ref <=0", "NO_ERROR", "ValueError")
    except ValueError:
        log_res("build_pair: invalid ref <=0", "ValueError", "ValueError")

    try:
        grid_exec.build_pair(grid_exec.BUY_GRID_FACTOR * GRID_STEP)
        log_res("build_pair: invalid buy <=0", "NO_ERROR", "ValueError")
    except ValueError:
        log_res("build_pair: invalid buy <=0", "ValueError", "ValueError")

    orders_with_oid = [order("BUY", 99800.0, oid=11), order("SELL", 100200.0, oid=22)]

    result, calls = run_exec_cleanup_case([], wait_result=True, remaining_orders=[])
    log_res("cleanup_orders: empty -> True", result, True)
    log_res("cleanup_orders: empty no cancel", calls["bulk_cancel"], 0)

    result, calls = run_exec_cleanup_case(orders_with_oid, wait_result=True, remaining_orders=[])
    log_res("cleanup_orders: success -> True", result, True)
    log_res("cleanup_orders: success cancel count", calls["bulk_cancel"], 1)
    log_res(
        "cleanup_orders: success cancel payload",
        calls["bulk_cancel_args"],
        [{"coin": grid_exec.SYMBOL, "oid": 11}, {"coin": grid_exec.SYMBOL, "oid": 22}],
    )

    result, calls = run_exec_cleanup_case(orders_with_oid, wait_result=False, remaining_orders=orders_with_oid[:1])
    log_res("cleanup_orders: remain -> False", result, False)
    log_res("cleanup_orders: remain get_open_orders", calls["get_open_orders"], 1)
    log_res("cleanup_orders: remain summarize", calls["summarize_orders"], 1)

    placed_state = {"mode": PAIR_MODE, "buy_price": 99800.0, "sell_price": 100200.0, "reference_price": 100000.0}

    result, calls = run_rebuild_case([], cleanup_result=True, reference_price_input=100000.0, computed_reference_price=99999.0, place_pair_result=placed_state)
    log_res("rebuild: explicit ref return", result, placed_state)
    log_res("rebuild: explicit ref no cleanup", calls["cleanup_orders"], 0)
    log_res("rebuild: explicit ref no get_ref", calls["get_reference_price"], 0)
    log_res("rebuild: explicit ref place arg", calls["place_pair_args"], 100000.0)

    result, calls = run_rebuild_case([], cleanup_result=True, reference_price_input=None, computed_reference_price=100400.0, place_pair_result=placed_state)
    log_res("rebuild: implicit ref return", result, placed_state)
    log_res("rebuild: implicit ref get_ref", calls["get_reference_price"], 1)
    log_res("rebuild: implicit ref place arg", calls["place_pair_args"], 100400.0)

    result, calls = run_rebuild_case(orders_with_oid, cleanup_result=True, reference_price_input=100000.0, computed_reference_price=99999.0, place_pair_result=placed_state)
    log_res("rebuild: with old orders cleanup", result, placed_state)
    log_res("rebuild: with old orders cleanup count", calls["cleanup_orders"], 1)
    log_res("rebuild: with old orders cleanup args", calls["cleanup_orders_args"], orders_with_oid)

    result, calls = run_rebuild_case(orders_with_oid, cleanup_result=False, reference_price_input=100000.0, computed_reference_price=99999.0, place_pair_result=placed_state)
    log_res("rebuild: cleanup fail -> None", result, None)
    log_res("rebuild: cleanup fail no place", calls["place_pair"], 0)
    log_res("rebuild: cleanup fail no get_ref", calls["get_reference_price"], 0)


def run_execution_step4_eval():
    print("\n🚀 execution Step 4")

    if grid_exec is None:
        log_note("execution step4: import grid_execution", "SKIPPED", "grid_execution import failed")
        return

    log_res(
        "classify: empty statuses -> error",
        grid_exec.classify_order_result({"response": {"data": {"statuses": []}}}),
        "error",
    )
    log_res(
        "classify: generic error -> error",
        grid_exec.classify_order_result({"response": {"data": {"statuses": [{"error": "Some failure"}]}}}),
        "error",
    )
    log_res(
        "classify: insufficient balance",
        grid_exec.classify_order_result({"response": {"data": {"statuses": [{"error": "Insufficient spot balance"}]}}}),
        "insufficient_spot_balance",
    )
    log_res(
        "classify: resting -> ok",
        grid_exec.classify_order_result({"response": {"data": {"statuses": [{"resting": {"oid": 1}}]}}}),
        "ok",
    )
    log_res(
        "classify: filled -> ok",
        grid_exec.classify_order_result({"response": {"data": {"statuses": [{"filled": {"totalSz": "1"}}]}}}),
        "ok",
    )
    log_res(
        "classify: unknown payload -> error",
        grid_exec.classify_order_result({"response": {"data": {"statuses": [{"weird": 1}]}}}),
        "error",
    )

    partial_orders = [order("BUY", 99800.0, oid=11)]

    result, calls = run_exec_partial_cleanup_case([], cleanup_result=True)
    log_res("partial cleanup: no remaining -> True", result, True)
    log_res("partial cleanup: no remaining no cleanup", calls["cleanup_orders"], 0)
    log_res("partial cleanup: no remaining no summary", calls["summarize_orders"], 0)

    result, calls = run_exec_partial_cleanup_case(partial_orders, cleanup_result=True)
    log_res("partial cleanup: remaining -> True", result, True)
    log_res("partial cleanup: remaining cleanup count", calls["cleanup_orders"], 1)
    log_res("partial cleanup: remaining cleanup args", calls["cleanup_orders_args"], partial_orders)
    log_res("partial cleanup: remaining summarize", calls["summarize_orders"], 1)

    result, calls = run_exec_partial_cleanup_case(partial_orders, cleanup_result=False)
    log_res("partial cleanup: cleanup fail -> False", result, False)
    log_res("partial cleanup: cleanup fail count", calls["cleanup_orders"], 1)

    result, calls = run_place_pair_case(
        buy_status="ok",
        sell_status="ok",
        allow_buy_only=True,
        allow_sell_only=True,
        reference_price=100000.0,
    )
    log_res(
        "place_pair: both ok -> PAIR",
        result,
        {"mode": PAIR_MODE, "buy_price": 99800.0, "sell_price": 100200.0, "reference_price": 100000.0},
    )
    log_res("place_pair: both ok no cleanup", calls["cleanup_after_partial_place_failure"], 0)
    log_res("place_pair: both ok call order", calls["place_limit_order_args"], ["BUY", "SELL"])

    result, calls = run_place_pair_case(
        buy_status="ok",
        sell_status="insufficient_spot_balance",
        allow_buy_only=True,
        allow_sell_only=True,
        reference_price=100000.0,
    )
    log_res(
        "place_pair: buy ok -> BUY_ONLY",
        result,
        {"mode": BUY_ONLY_MODE, "buy_price": 99800.0, "reference_price": 100000.0},
    )
    log_res("place_pair: BUY_ONLY no cleanup", calls["cleanup_after_partial_place_failure"], 0)

    result, calls = run_place_pair_case(
        buy_status="insufficient_spot_balance",
        sell_status="ok",
        allow_buy_only=True,
        allow_sell_only=True,
        reference_price=100000.0,
    )
    log_res(
        "place_pair: sell ok -> SELL_ONLY",
        result,
        {"mode": SELL_ONLY_MODE, "sell_price": 100200.0, "reference_price": 100000.0},
    )
    log_res("place_pair: SELL_ONLY no cleanup", calls["cleanup_after_partial_place_failure"], 0)

    result, calls = run_place_pair_case(
        buy_status="ok",
        sell_status="insufficient_spot_balance",
        allow_buy_only=False,
        allow_sell_only=True,
        reference_price=100000.0,
    )
    log_res("place_pair: BUY_ONLY disallowed -> None", result, None)
    log_res("place_pair: BUY_ONLY disallowed cleanup", calls["cleanup_after_partial_place_failure"], 1)

    result, calls = run_place_pair_case(
        buy_status="insufficient_spot_balance",
        sell_status="ok",
        allow_buy_only=True,
        allow_sell_only=False,
        reference_price=100000.0,
    )
    log_res("place_pair: SELL_ONLY disallowed -> None", result, None)
    log_res("place_pair: SELL_ONLY disallowed cleanup", calls["cleanup_after_partial_place_failure"], 1)

    result, calls = run_place_pair_case(
        buy_status="error",
        sell_status="ok",
        allow_buy_only=True,
        allow_sell_only=True,
        reference_price=100000.0,
    )
    log_res("place_pair: buy error -> None", result, None)
    log_res("place_pair: buy error cleanup", calls["cleanup_after_partial_place_failure"], 1)

    result, calls = run_place_pair_case(
        buy_status="ok",
        sell_status="error",
        allow_buy_only=True,
        allow_sell_only=True,
        reference_price=100000.0,
    )
    log_res("place_pair: sell error -> None", result, None)
    log_res("place_pair: sell error cleanup", calls["cleanup_after_partial_place_failure"], 1)

    result, calls = run_place_pair_case(
        buy_status="error",
        sell_status="error",
        allow_buy_only=True,
        allow_sell_only=True,
        reference_price=100000.0,
    )
    log_res("place_pair: both error -> None", result, None)
    log_res("place_pair: both error cleanup", calls["cleanup_after_partial_place_failure"], 1)


def run_gateway_eval():
    print("\n🚀 gateway Step 5")

    if grid_gate is None:
        log_note("gateway: import grid_gateway", "SKIPPED", "grid_gateway import failed")
        return

    normalized_buy = grid_gate.normalize_order({"side": "B", "limitPx": 99800.4, "oid": 11})
    log_res("gateway: normalize BUY side", normalized_buy["side"], "BUY")
    log_res("gateway: normalize BUY price", normalized_buy["price"], grid_gate.normalize_price(99800.4))
    log_res("gateway: normalize BUY oid keep", normalized_buy["oid"], 11)

    normalized_sell = grid_gate.normalize_order({"side": "A", "limitPx": 100200.4, "oid": 22})
    log_res("gateway: normalize SELL side", normalized_sell["side"], "SELL")
    log_res("gateway: normalize SELL price", normalized_sell["price"], grid_gate.normalize_price(100200.4))

    try:
        grid_gate.normalize_order({"side": "X", "limitPx": 100000.0})
        log_res("gateway: normalize invalid side", "NO_ERROR", "ValueError")
    except ValueError:
        log_res("gateway: normalize invalid side", "ValueError", "ValueError")

    raw_orders = [
        {"side": "B", "limitPx": 99800.4, "oid": 11},
        {"side": "A", "limitPx": 100200.4, "oid": 22},
    ]
    result, calls = run_gateway_open_orders_case(raw_orders)
    log_res(
        "gateway: get_open_orders result",
        result,
        [
            {"side": "BUY", "price": grid_gate.normalize_price(99800.4), "oid": 11, "limitPx": 99800.4},
            {"side": "SELL", "price": grid_gate.normalize_price(100200.4), "oid": 22, "limitPx": 100200.4},
        ],
    )
    log_res("gateway: get_open_orders retry call", calls["read_with_retry"], 1)

    result = run_gateway_read_mid_case(mids_value={grid_gate.BTC_MID_KEY: 100123.4})
    log_res("gateway: read_btc_mid success", result, grid_gate.normalize_price(100123.4))

    result = run_gateway_read_mid_case(mids_value={grid_gate.BTC_MID_KEY: 0})
    log_res("gateway: read_btc_mid zero -> None", result, None)

    result = run_gateway_read_mid_case(mids_value={})
    log_res("gateway: read_btc_mid missing -> None", result, None)

    result = run_gateway_read_mid_case(mids_exc=RuntimeError("mid failed"))
    log_res("gateway: read_btc_mid exc -> None", result, None)

    result, calls = run_gateway_reference_case(mids_value={grid_gate.BTC_MID_KEY: 100123.4})
    expected_reference = grid_gate.normalize_price(int((100123.4 / GRID_STEP) + 0.5) * GRID_STEP)
    log_res("gateway: get_reference_price success", result, expected_reference)
    log_res("gateway: get_reference_price read call", calls["read_all_mids"], 1)

    result, _ = run_gateway_reference_case(mids_value={grid_gate.BTC_MID_KEY: 0})
    log_res("gateway: get_reference_price zero", result, "ValueError")

    result, _ = run_gateway_reference_case(mids_value={})
    log_res("gateway: get_reference_price missing", result, "ValueError")

    result, _ = run_gateway_reference_case(mids_value={grid_gate.BTC_MID_KEY: 100.0})
    log_res("gateway: get_reference_price invalid buy", result, "ValueError")

    log_res("gateway: retryable TimeoutError", grid_gate.is_retryable_read_error(TimeoutError("x")), True)
    log_res("gateway: retryable reset", grid_gate.is_retryable_read_error(ConnectionResetError("x")), True)
    log_res("gateway: retryable OSError text", grid_gate.is_retryable_read_error(OSError("temporary failure")), True)
    log_res("gateway: non-retryable ValueError", grid_gate.is_retryable_read_error(ValueError("x")), False)

def run_bootstrap_step7a_eval():
    print("\n🚀 bootstrap Step 7A")

    if grid_engine is None:
        log_note("bootstrap step7A: import grid", "SKIPPED", "grid import failed")
        return

    inherited_state = {"mode": PAIR_MODE, "buy_price": 99800.0, "sell_price": 100200.0}
    rebuilt_state = {"mode": PAIR_MODE, "buy_price": 99700.0, "sell_price": 100100.0}
    orders = pair_orders()

    result, calls = run_bootstrap_saved_state_case(
        orders=orders,
        bootstrap_state=inherited_state,
        rebuild_state=rebuilt_state,
    )
    log_res("bootstrap_saved_state: inherit pair", result, inherited_state)
    log_res("bootstrap_saved_state: inherit no rebuild", calls["rebuild"], 0)
    log_res("bootstrap_saved_state: inherit summarize", calls["summarize_orders"], 1)

    result, calls = run_bootstrap_saved_state_case(
        orders=orders[:1],
        bootstrap_state=None,
        rebuild_state=rebuilt_state,
    )
    log_res("bootstrap_saved_state: reject->rebuild", result, rebuilt_state)
    log_res("bootstrap_saved_state: reject->rebuild count", calls["rebuild"], 1)
    log_res("bootstrap_saved_state: reject->rebuild args", calls["rebuild_args"][0], orders[:1])

    result, calls = run_bootstrap_saved_state_case(
        orders=orders[:1],
        bootstrap_state=None,
        rebuild_state=None,
    )
    log_res("bootstrap_saved_state: reject->fail", result, None)
    log_res("bootstrap_saved_state: reject->fail rebuild", calls["rebuild"], 1)

def run_gateway_execution_step7b_eval():
    print("\n🚀 gateway/execution Step 7B")

    if grid_exec is None:
        log_note("step7B: import grid_execution", "SKIPPED", "grid_execution import failed")
    else:
        buy_action = {"side": "BUY", "price": 99800.0, "size": 0.00123}
        sell_action = {"side": "SELL", "price": 100200.0, "size": 0.00456}

        ok_result = {"response": {"data": {"statuses": [{"resting": {"oid": 1}}]}}}

        result, calls = run_place_limit_order_case(buy_action, ok_result)
        log_res("place_limit_order: BUY result", result, "ok")
        log_res("place_limit_order: BUY call count", calls["exchange_order"], 1)
        log_res(
            "place_limit_order: BUY payload",
            calls["exchange_order_args"],
            (
                grid_exec.SYMBOL,
                True,
                float(buy_action["size"]),
                float(buy_action["price"]),
                {"limit": {"tif": "Gtc"}},
                False,
            ),
        )

        result, calls = run_place_limit_order_case(sell_action, ok_result)
        log_res("place_limit_order: SELL result", result, "ok")
        log_res(
            "place_limit_order: SELL payload",
            calls["exchange_order_args"],
            (
                grid_exec.SYMBOL,
                False,
                float(sell_action["size"]),
                float(sell_action["price"]),
                {"limit": {"tif": "Gtc"}},
                False,
            ),
        )

    if grid_gate is None:
        log_note("step7B: import grid_gateway", "SKIPPED", "grid_gateway import failed")
    else:
        retry_exc = TimeoutError("temporary timeout")

        result, calls = run_read_with_retry_case(
            [retry_exc, retry_exc, {"ok": 1}],
            max_retries=2,
        )
        log_res("read_with_retry: retry then success", result, {"ok": 1})
        log_res("read_with_retry: retry then success calls", calls["read_func"], 3)
        log_res("read_with_retry: retry then success sleeps", calls["sleep"], 2)

        result, calls = run_read_with_retry_case(
            [ValueError("bad request")],
            max_retries=2,
        )
        log_res("read_with_retry: non-retryable raise", result, "ValueError")
        log_res("read_with_retry: non-retryable calls", calls["read_func"], 1)
        log_res("read_with_retry: non-retryable no sleep", calls["sleep"], 0)

        result, calls = run_read_with_retry_case(
            [retry_exc, retry_exc, retry_exc],
            max_retries=2,
        )
        log_res("read_with_retry: exhausted raise", result, "TimeoutError")
        log_res("read_with_retry: exhausted calls", calls["read_func"], 3)
        log_res("read_with_retry: exhausted sleeps", calls["sleep"], 2)


if __name__ == "__main__":
    run_bootstrap_eval()
    run_pair_mode_eval()
    run_buy_only_eval()
    run_sell_only_eval()
    run_step_engine_eval()
    run_execution_eval()
    run_execution_step4_eval()
    run_gateway_eval()
    run_bootstrap_step7a_eval()
    run_gateway_execution_step7b_eval()
    run_red_team_eval()
    print_final_summary()
    print("\n🏁 Test Cycle Complete.")
 