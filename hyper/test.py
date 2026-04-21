from grid_config import (
    GRID_STEP,
    BUDGET_USDC,
    PAIR_MODE,
    BUY_ONLY_MODE,
    SELL_ONLY_MODE,
    ABNORMAL_MODE,
    REANCHOR_BREAK_STEPS,
    BUY_GRID_FACTOR,
    WAIT_NO_OPEN_ORDERS_INTERVAL_SEC,
    API_WALLET_KEY,
)
from grid_decision import classify_order_mode, decide_cycle_action

try:
    import grid as grid_engine
except Exception:
    grid_engine = None

try:
    import grid_execution as grid_exec
except Exception:
    grid_exec = None

try:
    import grid_config as grid_gate
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
    return classify_order_mode(orders)["mode"]


def order(side, price, size=None, oid=None):
    o = {"side": side, "price": float(price)}
    if size is not None:
        o["size"] = float(size)
    if oid is not None:
        o["oid"] = oid
    return o


def pair_orders(buy_price=9800.0, sell_price=10200.0, buy_size=None, sell_size=None):
    return [
        order("BUY", buy_price, buy_size),
        order("SELL", sell_price, sell_size),
    ]


def buy_only_orders(price=9800.0, size=None):
    return [order("BUY", price, size)]


def sell_only_orders(price=10200.0, size=None):
    return [order("SELL", price, size)]


def live_snapshot(orders, btc_mid=None):
    return {
        "orders": orders,
        "btc_mid": btc_mid,
        **classify_order_mode(orders),
    }


def pair_state(buy_price=9800.0, sell_price=10200.0):
    return {
        "mode": PAIR_MODE,
        "buy_price": float(buy_price),
        "sell_price": float(sell_price),
    }


def buy_only_state(buy_price=9800.0):
    return {
        "mode": BUY_ONLY_MODE,
        "buy_price": float(buy_price),
    }


def sell_only_state(sell_price=10200.0):
    return {
        "mode": SELL_ONLY_MODE,
        "sell_price": float(sell_price),
    }

def run_bootstrap_saved_state_case(orders, bootstrap_state, rebuild_state):
    if grid_engine is None:
        return None, None

    calls = {
        "read_orders": 0,
        "summarize_orders": 0,
        "classify_order_mode": 0,
        "rebuild": 0,
        "rebuild_args": None,
    }

    old_read_orders = grid_engine.read_orders
    old_summarize_orders = grid_engine.summarize_orders
    old_classify_order_mode = grid_engine.classify_order_mode
    old_rebuild = grid_engine.rebuild
    old_info = grid_engine.Info
    old_exchange = grid_engine.Exchange
    old_account = grid_engine.Account

    class FakeAccount:
        @staticmethod
        def from_key(key):
            return object()

    def fake_info(api_url):
        return object()

    def fake_exchange(account, api_url, account_address=None):
        return object()

    def fake_read_orders(info):
        calls["read_orders"] += 1
        return orders

    def fake_summarize_orders(local_orders):
        calls["summarize_orders"] += 1
        return 0, 0

    def fake_classify_order_mode(local_orders):
        calls["classify_order_mode"] += 1
        if bootstrap_state is not None:
            return bootstrap_state
        return classify_order_mode(local_orders)

    def fake_rebuild(info, trader, local_snapshot, strategy="reset", rebuild_price=None):
        calls["rebuild"] += 1
        calls["rebuild_args"] = (local_snapshot, strategy, rebuild_price)
        return rebuild_state

    try:
        grid_engine.read_orders = fake_read_orders
        grid_engine.summarize_orders = fake_summarize_orders
        grid_engine.classify_order_mode = fake_classify_order_mode
        grid_engine.rebuild = fake_rebuild
        grid_engine.Info = fake_info
        grid_engine.Exchange = fake_exchange
        grid_engine.Account = FakeAccount
        _, _, result = grid_engine.bootstrap()
    finally:
        grid_engine.read_orders = old_read_orders
        grid_engine.summarize_orders = old_summarize_orders
        grid_engine.classify_order_mode = old_classify_order_mode
        grid_engine.rebuild = old_rebuild
        grid_engine.Info = old_info
        grid_engine.Exchange = old_exchange
        grid_engine.Account = old_account

    return result, calls


def run_place_limit_order_case(action, order_result):
    if grid_exec is None:
        return None, None

    calls = {
        "trader_order": 0,
        "trader_order_args": None,
    }

    class FakeTrader:
        def order(self, symbol, is_buy, size, price, tif_payload, reduce_only):
            calls["trader_order"] += 1
            calls["trader_order_args"] = (
                symbol,
                is_buy,
                size,
                price,
                tif_payload,
                reduce_only,
            )
            return order_result

    result = grid_exec.place_limit_order(FakeTrader(), action)
    return result, calls


def run_retry_read_case(sequence, name="test-read", retries=2):
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
            result = grid_gate.retry_read(
                name,
                fake_read,
                retries=retries,
                delay=0.1,
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
        "read_orders": 0,
        "summarize_orders": 0,
        "rebuild": 0,
        "rebuild_args": None,
    }

    old_read_orders = grid_engine.read_orders
    old_summarize_orders = grid_engine.summarize_orders
    old_rebuild = grid_engine.rebuild
    old_info = grid_engine.Info
    old_exchange = grid_engine.Exchange
    old_account = grid_engine.Account

    class FakeAccount:
        @staticmethod
        def from_key(key):
            return object()

    def fake_info(api_url):
        return object()

    def fake_exchange(account, api_url, account_address=None):
        return object()

    def fake_read_orders(info):
        calls["read_orders"] += 1
        return orders

    def fake_summarize_orders(local_orders):
        calls["summarize_orders"] += 1
        return 0, 0

    def fake_rebuild(info, trader, local_snapshot, strategy="reset", rebuild_price=None):
        calls["rebuild"] += 1
        calls["rebuild_args"] = (local_snapshot, strategy, rebuild_price)
        return rebuild_state

    try:
        grid_engine.read_orders = fake_read_orders
        grid_engine.summarize_orders = fake_summarize_orders
        grid_engine.rebuild = fake_rebuild
        grid_engine.Info = fake_info
        grid_engine.Exchange = fake_exchange
        grid_engine.Account = FakeAccount
        _, _, result = grid_engine.bootstrap()
    finally:
        grid_engine.read_orders = old_read_orders
        grid_engine.summarize_orders = old_summarize_orders
        grid_engine.rebuild = old_rebuild
        grid_engine.Info = old_info
        grid_engine.Exchange = old_exchange
        grid_engine.Account = old_account

    return result, calls


def run_cleanup_orders_missing_oid_case(orders):
    if grid_exec is None:
        return None

    class FakeTrader:
        def cancel(self, symbol, oid):
            return None

    try:
        grid_exec.cleanup_orders(info=object(), trader=FakeTrader(), orders=orders)
        return "NO_ERROR"
    except Exception as exc:
        return type(exc).__name__


def run_retry_read_status_case(exc_obj, retries=2):
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
            grid_gate.retry_read(
                "status-case",
                fake_read,
                retries=retries,
                delay=0.1,
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
        inherited_state = {"mode": PAIR_MODE, "buy_price": 9800.0, "sell_price": 10200.0}

        result, calls = run_bootstrap_saved_state_real_case(
            orders=pair_orders(),
            rebuild_state={"mode": "SHOULD_NOT_HAPPEN"},
        )
        log_res(
            "red-team: bootstrap real inherit",
            (result["mode"], result["buy_price"], result["sell_price"]),
            (PAIR_MODE, 9800.0, 10200.0),
        )
        log_res("red-team: bootstrap real no rebuild", calls["rebuild"], 0)

        result, calls = run_bootstrap_saved_state_real_case(
            orders=pair_orders(9900.0, 10000.0),  # wrong gap, real decision should reject
            rebuild_state={"mode": PAIR_MODE, "buy_price": 9700.0, "sell_price": 10100.0},
        )
        log_res("red-team: bootstrap real reject->rebuild", result, {"mode": PAIR_MODE, "buy_price": 9700.0, "sell_price": 10100.0})
        log_res("red-team: bootstrap real reject rebuild", calls["rebuild"], 1)

    if grid_exec is None:
        log_note("red-team: cleanup missing oid", "SKIPPED", "grid_execution import failed")
    else:
        # 这不是你想要的行为，但很可能是真实会暴露的弱点
        result = run_cleanup_orders_missing_oid_case([{"side": "BUY", "price": 9800.0}])
        log_note("red-team: cleanup missing oid", result, "cleanup_orders assumes every order has oid")

        result = run_cleanup_orders_missing_oid_case([{"side": "BUY", "price": 9800.0, "oid": 11}])
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

        result, calls = run_retry_read_status_case(RetryableHTTPError(502), retries=2)
        log_res("red-team: retryable status exhausted", result, "RetryableHTTPError")
        log_res("red-team: retryable status calls", calls["read_func"], 3)
        log_res("red-team: retryable status sleeps", calls["sleep"], 2)

        result, calls = run_retry_read_status_case(NonRetryableHTTPError(400), retries=2)
        log_res("red-team: nonretry status direct", result, "NonRetryableHTTPError")
        log_res("red-team: nonretry status calls", calls["read_func"], 1)
        log_res("red-team: nonretry status no sleep", calls["sleep"], 0)

        # 这个更像“逻辑风险暴露”，不是 mismatch
        state_b = buy_only_state(9800.0)
        live_buy_drift = buy_only_orders(9000.0)
        result = decide_cycle_action(
            live_snapshot(live_buy_drift, 9000.0 + (BUY_GRID_FACTOR + REANCHOR_BREAK_STEPS) * GRID_STEP - 1),
            state_b,
        )
        log_note("red-team: BUY_ONLY drift still keep", result, "saved_state drift remains silent if anchor-break not triggered")

def run_order_mode_classification_eval():
    print(f"🚀 Order Mode Classification Evaluation (GRID_STEP={GRID_STEP})")

    o_valid = pair_orders()

    log_res("Order Mode: Valid Pair", mode_of_bootstrap(o_valid), PAIR_MODE)
    classified_pair_state = classify_order_mode(o_valid)
    log_res("Order Mode: Pair State", classified_pair_state, pair_state())
    log_res("Order Mode: Reversed Pair", mode_of_bootstrap([o_valid[1], o_valid[0]]), PAIR_MODE)
    log_res("Order Mode: Partial", mode_of_bootstrap(o_valid[:1]), BUY_ONLY_MODE)
    log_res("Order Mode: Empty", mode_of_bootstrap([]), ABNORMAL_MODE)
    log_res("Order Mode: >2 Orders", mode_of_bootstrap(o_valid + [order("BUY", 9600.0)]), ABNORMAL_MODE)

    log_res("Order Mode: Two BUY", mode_of_bootstrap([order("BUY", 9800.0), order("BUY", 9600.0)]), ABNORMAL_MODE)
    log_res("Order Mode: Two SELL", mode_of_bootstrap([order("SELL", 10200.0), order("SELL", 10400.0)]), ABNORMAL_MODE)
    log_res("Order Mode: buy>=sell", mode_of_bootstrap(pair_orders(10200.0, 9800.0)), ABNORMAL_MODE)
    log_res("Order Mode: HOLD", mode_of_bootstrap([order("HOLD", 10200.0)]), ABNORMAL_MODE)
    log_res("Order Mode: sell", mode_of_bootstrap([order("sell", 10200.0)]), ABNORMAL_MODE)
    log_res("Order Mode: Correct Gap", mode_of_bootstrap(pair_orders(9800.0, 10200.0)), PAIR_MODE)
    log_res("Order Mode: Wrong Gap", mode_of_bootstrap(pair_orders(9900.0, 10000.0)), ABNORMAL_MODE)

    log_note(
        "Order Mode: Size Mismatch",
        mode_of_bootstrap(pair_orders(9800.0, 10200.0, 1.0, 0.00001)),
        "no size validation",
    )

    log_res("Config: API Wallet Key", "Exists" if API_WALLET_KEY else "Missing", "Exists")


def run_pair_mode_eval():
    print("\n🚀 PAIR Mode")

    o_pair = pair_orders()
    state_p = pair_state()

    log_res("PAIR: SELL filled", decide_cycle_action(live_snapshot(o_pair[:1]), state_p), ("rebuild", "done_deal", 10200.0))
    log_res("PAIR: BUY filled", decide_cycle_action(live_snapshot(o_pair[1:]), state_p), ("rebuild", "done_deal", 9800.0))
    log_res("PAIR: Keep", decide_cycle_action(live_snapshot(o_pair), state_p), ("keep", None, None))

    log_res("PAIR: 0 orders", decide_cycle_action(live_snapshot([]), state_p), ("abnormal", None, None))
    log_res("PAIR: same side", decide_cycle_action(live_snapshot([order("BUY", 9800.0), order("BUY", 9700.0)]), state_p), ("abnormal", None, None))
    log_res("PAIR: drift", decide_cycle_action(live_snapshot(pair_orders(9000.0, 10200.0)), state_p), ("abnormal", None, None))
    log_res("PAIR: >2 orders", decide_cycle_action(live_snapshot(o_pair + [order("BUY", 9600.0)]), state_p), ("abnormal", None, None))


def run_buy_only_eval():
    print("\n🚀 BUY_ONLY Mode")

    state_b = buy_only_state(9800.0)
    live_buy = buy_only_orders(9800.0)

    log_res("BUY_ONLY: fill -> rebuild", decide_cycle_action(live_snapshot([]), state_b), ("rebuild", "done_deal", 9800.0))
    log_res("BUY_ONLY: keep", decide_cycle_action(live_snapshot(live_buy), state_b), ("keep", None, None))

    log_note(
        "BUY_ONLY: price drift",
        decide_cycle_action(live_snapshot(buy_only_orders(9700.0)), state_b),
        "no price validation",
    )

    distance = (BUY_GRID_FACTOR + REANCHOR_BREAK_STEPS) * GRID_STEP
    log_res("BUY_ONLY: break < threshold", decide_cycle_action(live_snapshot(live_buy, 9800.0 + distance - 1.0), state_b), ("keep", None, None))
    log_res("BUY_ONLY: break == threshold", decide_cycle_action(live_snapshot(live_buy, 9800.0 + distance), state_b), ("rebuild", "anchor_break", None))
    log_res("BUY_ONLY: break > threshold", decide_cycle_action(live_snapshot(live_buy, 9800.0 + distance + 1.0), state_b), ("rebuild", "anchor_break", None))

    log_res("BUY_ONLY: mid None", decide_cycle_action(live_snapshot(live_buy, None), state_b), ("keep", None, None))
    log_res("BUY_ONLY: mid <=0", decide_cycle_action(live_snapshot(live_buy, 0.0), state_b), ("keep", None, None))

    log_res("BUY_ONLY: wrong side", decide_cycle_action(live_snapshot(sell_only_orders()), state_b), ("abnormal", None, None))
    log_res("BUY_ONLY: pair snapshot", decide_cycle_action(live_snapshot(pair_orders()), state_b), ("abnormal", None, None))
    log_res("BUY_ONLY: multiple BUY", decide_cycle_action(live_snapshot([order("BUY", 9800.0), order("BUY", 9700.0)]), state_b), ("abnormal", None, None))


def run_sell_only_eval():
    print("\n🚀 SELL_ONLY Mode")

    state_s = sell_only_state(10200.0)
    live_sell = sell_only_orders(10200.0)

    log_res("SELL_ONLY: fill -> rebuild", decide_cycle_action(live_snapshot([]), state_s), ("rebuild", "done_deal", 10200.0))
    log_res("SELL_ONLY: keep", decide_cycle_action(live_snapshot(live_sell), state_s), ("keep", None, None))

    log_note(
        "SELL_ONLY: price drift",
        decide_cycle_action(live_snapshot(sell_only_orders(10300.0)), state_s),
        "no price validation",
    )

    log_res("SELL_ONLY: wrong side", decide_cycle_action(live_snapshot(buy_only_orders()), state_s), ("abnormal", None, None))
    log_res("SELL_ONLY: pair snapshot", decide_cycle_action(live_snapshot(pair_orders()), state_s), ("abnormal", None, None))
    log_res("SELL_ONLY: multiple SELL", decide_cycle_action(live_snapshot([order("SELL", 10200.0), order("SELL", 10400.0)]), state_s), ("abnormal", None, None))


def run_run_cycle_case(saved_state, orders, action_result, rebuild_result=None, btc_mid=10500.0):
    if grid_engine is None:
        return None, None

    calls = {
        "read_orders": 0,
        "read_btc_mid": 0,
        "decide_cycle_action": 0,
        "rebuild": 0,
        "rebuild_args": None,
        "btc_mid_seen": None,
    }

    old_read_orders = grid_engine.read_orders
    old_read_btc_mid = grid_engine.read_btc_mid
    old_decide_cycle_action = grid_engine.decide_cycle_action
    old_rebuild = grid_engine.rebuild

    def fake_read_orders(info):
        calls["read_orders"] += 1
        return orders

    def fake_read_btc_mid(info):
        calls["read_btc_mid"] += 1
        return btc_mid

    def fake_decide_cycle_action(live_snapshot, saved_state):
        calls["decide_cycle_action"] += 1
        calls["btc_mid_seen"] = live_snapshot["btc_mid"]
        return action_result

    def fake_rebuild(info, trader, local_snapshot, strategy="reset", rebuild_price=None):
        calls["rebuild"] += 1
        calls["rebuild_args"] = (local_snapshot, strategy, rebuild_price)
        return rebuild_result

    try:
        grid_engine.read_orders = fake_read_orders
        grid_engine.read_btc_mid = fake_read_btc_mid
        grid_engine.decide_cycle_action = fake_decide_cycle_action
        grid_engine.rebuild = fake_rebuild
        result = grid_engine.run_cycle(info=object(), trader=object(), saved_state=saved_state)
    finally:
        grid_engine.read_orders = old_read_orders
        grid_engine.read_btc_mid = old_read_btc_mid
        grid_engine.decide_cycle_action = old_decide_cycle_action
        grid_engine.rebuild = old_rebuild

    return result, calls


def run_run_cycle_eval():
    print("\n🚀 run_cycle Mode")

    if grid_engine is None:
        log_note("run_cycle: import grid", "SKIPPED", "grid import failed")
        return

    state_p = pair_state()
    state_b = buy_only_state()
    state_s = sell_only_state()
    orders_p = pair_orders()
    rebuilt_state = {"mode": PAIR_MODE, "buy_price": 9700.0, "sell_price": 10100.0}

    result, calls = run_run_cycle_case(state_p, orders_p, ("keep", None, None))
    log_res("run_cycle: PAIR keep return", result, state_p)
    log_res("run_cycle: PAIR keep no rebuild", calls["rebuild"], 0)
    log_res("run_cycle: PAIR keep no mid read", calls["read_btc_mid"], 0)

    result, calls = run_run_cycle_case(state_p, orders_p[:1], ("rebuild", "done_deal", 10200.0), rebuild_result=rebuilt_state)
    log_res("run_cycle: PAIR rebuild return", result, rebuilt_state)
    log_res("run_cycle: PAIR rebuild count", calls["rebuild"], 1)
    log_res("run_cycle: PAIR rebuild strategy", calls["rebuild_args"][1], "done_deal")
    log_res("run_cycle: PAIR rebuild ref", calls["rebuild_args"][2], 10200.0)

    result, calls = run_run_cycle_case(state_p, orders_p[:1], ("rebuild", "done_deal", 10200.0), rebuild_result=None)
    log_res("run_cycle: PAIR rebuild fail", result, None)
    log_res("run_cycle: PAIR rebuild fail count", calls["rebuild"], 1)

    result, calls = run_run_cycle_case(state_p, [], ("abnormal", None, None))
    log_res("run_cycle: PAIR abnormal return", result, None)
    log_res("run_cycle: PAIR abnormal no rebuild", calls["rebuild"], 0)

    result, calls = run_run_cycle_case(state_b, buy_only_orders(), ("keep", None, None), btc_mid=10250.0)
    log_res("run_cycle: BUY_ONLY keep return", result, state_b)
    log_res("run_cycle: BUY_ONLY read mid", calls["read_btc_mid"], 1)
    log_res("run_cycle: BUY_ONLY pass mid", calls["btc_mid_seen"], 10250.0)

    result, calls = run_run_cycle_case(state_s, sell_only_orders(), ("keep", None, None), btc_mid=10250.0)
    log_res("run_cycle: SELL_ONLY keep return", result, state_s)
    log_res("run_cycle: SELL_ONLY no mid read", calls["read_btc_mid"], 0)
    log_res("run_cycle: SELL_ONLY pass mid", calls["btc_mid_seen"], None)


def run_exec_cleanup_case(initial_orders, wait_result, remaining_orders):
    if grid_exec is None:
        return None, None

    calls = {
        "cancel": 0,
        "cancel_args": [],
        "wait_until": 0,
        "read_orders": 0,
        "summarize_orders": 0,
    }

    old_wait_until = grid_exec.wait_until
    old_read_orders = grid_exec.read_orders
    old_summarize_orders = grid_exec.summarize_orders

    class FakeTrader:
        def cancel(self, symbol, oid):
            calls["cancel"] += 1
            calls["cancel_args"].append((symbol, oid))

    def fake_wait_until(info, predicate, max_tries=10, interval_sec=WAIT_NO_OPEN_ORDERS_INTERVAL_SEC):
        calls["wait_until"] += 1
        return wait_result

    def fake_read_orders(info):
        calls["read_orders"] += 1
        return remaining_orders

    def fake_summarize_orders(orders):
        calls["summarize_orders"] += 1
        return 0, 0

    try:
        grid_exec.wait_until = fake_wait_until
        grid_exec.read_orders = fake_read_orders
        grid_exec.summarize_orders = fake_summarize_orders
        result = grid_exec.cleanup_orders(info=object(), trader=FakeTrader(), orders=initial_orders)
    finally:
        grid_exec.wait_until = old_wait_until
        grid_exec.read_orders = old_read_orders
        grid_exec.summarize_orders = old_summarize_orders

    return result, calls


def run_rebuild_case(initial_orders, cleanup_result, rebuild_price_input, computed_price, place_reset_rebuild_result):
    if grid_exec is None:
        return None, None

    calls = {
        "cleanup_orders": 0,
        "cleanup_orders_args": None,
        "read_btc_grid": 0,
        "place_reset_rebuild": 0,
        "place_reset_rebuild_args": None,
    }

    old_cleanup_orders = grid_exec.cleanup_orders
    old_read_btc_grid = grid_exec.read_btc_grid
    old_place_reset_rebuild = grid_exec.place_reset_rebuild

    def fake_cleanup_orders(info, trader, orders):
        calls["cleanup_orders"] += 1
        calls["cleanup_orders_args"] = orders
        return cleanup_result

    def fake_read_btc_grid(info):
        calls["read_btc_grid"] += 1
        return computed_price

    def fake_place_reset_rebuild(info, trader, price):
        calls["place_reset_rebuild"] += 1
        calls["place_reset_rebuild_args"] = price
        return place_reset_rebuild_result

    try:
        grid_exec.cleanup_orders = fake_cleanup_orders
        grid_exec.read_btc_grid = fake_read_btc_grid
        grid_exec.place_reset_rebuild = fake_place_reset_rebuild
        result = grid_exec.rebuild(
            info=object(),
            trader=object(),
            live_snapshot=live_snapshot(initial_orders),
            rebuild_price=rebuild_price_input,
        )
    finally:
        grid_exec.cleanup_orders = old_cleanup_orders
        grid_exec.read_btc_grid = old_read_btc_grid
        grid_exec.place_reset_rebuild = old_place_reset_rebuild

    return result, calls


def run_place_reset_rebuild_case(buy_status, sell_status, allow_buy_only, allow_sell_only, cleanup_result=True, price=10000.0):
    if grid_exec is None:
        return None, None

    calls = {
        "build_pair": 0,
        "place_limit_order": 0,
        "place_limit_order_args": [],
        "cleanup_orders": 0,
    }

    old_build_pair = grid_exec.build_pair
    old_place_limit_order = grid_exec.place_limit_order
    old_cleanup_orders = grid_exec.cleanup_orders
    old_allow_buy_only = grid_exec.ALLOW_BUY_ONLY_WHEN_NO_BTC
    old_allow_sell_only = grid_exec.ALLOW_SELL_ONLY_WHEN_NO_USDC

    buy_action = {"side": "BUY", "price": 9800.0, "size": 0.001}
    sell_action = {"side": "SELL", "price": 10200.0, "size": 0.001}

    statuses = [buy_status, sell_status]

    def fake_build_pair(local_price):
        calls["build_pair"] += 1
        return buy_action, sell_action

    def fake_place_limit_order(trader, action):
        calls["place_limit_order"] += 1
        calls["place_limit_order_args"].append(action["side"])
        return statuses.pop(0)

    def fake_cleanup_orders(info, trader):
        calls["cleanup_orders"] += 1
        return cleanup_result

    try:
        grid_exec.build_pair = fake_build_pair
        grid_exec.place_limit_order = fake_place_limit_order
        grid_exec.cleanup_orders = fake_cleanup_orders
        grid_exec.ALLOW_BUY_ONLY_WHEN_NO_BTC = allow_buy_only
        grid_exec.ALLOW_SELL_ONLY_WHEN_NO_USDC = allow_sell_only
        result = grid_exec.place_reset_rebuild(info=object(), trader=object(), price=price)
    finally:
        grid_exec.build_pair = old_build_pair
        grid_exec.place_limit_order = old_place_limit_order
        grid_exec.cleanup_orders = old_cleanup_orders
        grid_exec.ALLOW_BUY_ONLY_WHEN_NO_BTC = old_allow_buy_only
        grid_exec.ALLOW_SELL_ONLY_WHEN_NO_USDC = old_allow_sell_only

    return result, calls


def run_gateway_open_orders_case(raw_orders):
    if grid_gate is None:
        return None, None

    calls = {
        "retry_read": 0,
    }

    old_retry_read = grid_gate.retry_read

    def fake_retry_read(name, fn, retries=grid_gate.MAX_RETRIES, delay=grid_gate.RETRY_SEC):
        calls["retry_read"] += 1
        return raw_orders

    try:
        grid_gate.retry_read = fake_retry_read
        result = grid_gate.read_orders(info=object())
    finally:
        grid_gate.retry_read = old_retry_read

    return result, calls


def run_gateway_read_mid_case(mids_value=None, mids_exc=None):
    if grid_gate is None:
        return None

    class FakeInfo:
        def all_mids(self):
            if mids_exc is not None:
                raise mids_exc
            return mids_value

    try:
        return grid_gate.read_btc_mid(info=FakeInfo())
    except Exception as exc:
        return type(exc).__name__


def run_gateway_reference_case(mids_value=None, mids_exc=None):
    if grid_gate is None:
        return None, None

    calls = {
        "all_mids": 0,
    }

    class FakeInfo:
        def all_mids(self):
            calls["all_mids"] += 1
            if mids_exc is not None:
                raise mids_exc
            return mids_value

    try:
        result = grid_gate.read_btc_grid(info=FakeInfo())
        return result, calls
    except Exception as exc:
        return type(exc).__name__, calls


def run_execution_eval():
    print("\n🚀 execution Mode")

    if grid_exec is None:
        log_note("execution: import grid_execution", "SKIPPED", "grid_execution import failed")
        return

    buy_action, sell_action = grid_exec.build_pair(10000.0)
    log_res("build_pair: buy price", buy_action["price"], 10000.0 - grid_exec.BUY_GRID_FACTOR * GRID_STEP)
    log_res("build_pair: sell price", sell_action["price"], 10000.0 + grid_exec.SELL_GRID_FACTOR * GRID_STEP)
    log_res("build_pair: buy size", buy_action["size"], round(grid_exec.BUDGET_USDC / buy_action["price"], 5))
    log_res("build_pair: sell size", sell_action["size"], round(grid_exec.BUDGET_USDC / sell_action["price"], 5))

    old_buy_grid_factor = grid_exec.BUY_GRID_FACTOR
    old_sell_grid_factor = grid_exec.SELL_GRID_FACTOR
    try:
        grid_exec.BUY_GRID_FACTOR = 1.5
        grid_exec.SELL_GRID_FACTOR = 2.0
        buy_action, sell_action = grid_exec.build_pair(10000.0)
        log_res("build_pair: grid step buy factor", buy_action["price"], 10000.0 - 1.5 * GRID_STEP)
        log_res("build_pair: grid step sell factor", sell_action["price"], 10000.0 + 2.0 * GRID_STEP)
    finally:
        grid_exec.BUY_GRID_FACTOR = old_buy_grid_factor
        grid_exec.SELL_GRID_FACTOR = old_sell_grid_factor

    try:
        grid_exec.build_pair(0.0)
        log_res("build_pair: invalid price <=0", "NO_ERROR", "ValueError")
    except ValueError:
        log_res("build_pair: invalid price <=0", "ValueError", "ValueError")

    try:
        grid_exec.build_pair(grid_exec.BUY_GRID_FACTOR * GRID_STEP)
        log_res("build_pair: invalid buy <=0", "NO_ERROR", "ValueError")
    except ValueError:
        log_res("build_pair: invalid buy <=0", "ValueError", "ValueError")

    orders_with_oid = [order("BUY", 9800.0, oid=11), order("SELL", 10200.0, oid=22)]

    result, calls = run_exec_cleanup_case([], wait_result=True, remaining_orders=[])
    log_res("cleanup_orders: empty -> True", result, True)
    log_res("cleanup_orders: empty no cancel", calls["cancel"], 0)

    result, calls = run_exec_cleanup_case(orders_with_oid, wait_result=True, remaining_orders=[])
    log_res("cleanup_orders: success -> True", result, True)
    log_res("cleanup_orders: success cancel count", calls["cancel"], 2)
    log_res(
        "cleanup_orders: success cancel payload",
        calls["cancel_args"],
        [(grid_exec.SYMBOL, 11), (grid_exec.SYMBOL, 22)],
    )

    result, calls = run_exec_cleanup_case(orders_with_oid, wait_result=False, remaining_orders=orders_with_oid[:1])
    log_res("cleanup_orders: remain -> False", result, False)
    log_res("cleanup_orders: remain read_orders", calls["read_orders"], 1)
    log_res("cleanup_orders: remain summarize", calls["summarize_orders"], 1)

    placed_state = {"mode": PAIR_MODE, "buy_price": 9800.0, "sell_price": 10200.0, "reference_price": 10000.0}

    result, calls = run_rebuild_case([], cleanup_result=True, rebuild_price_input=10000.0, computed_price=9999.0, place_reset_rebuild_result=placed_state)
    log_res("rebuild: explicit price return", result, placed_state)
    log_res("rebuild: explicit price no cleanup", calls["cleanup_orders"], 0)
    log_res("rebuild: explicit price no read_btc_grid", calls["read_btc_grid"], 0)
    log_res("rebuild: explicit price place_reset_rebuild arg", calls["place_reset_rebuild_args"], 10000.0)

    result, calls = run_rebuild_case([], cleanup_result=True, rebuild_price_input=None, computed_price=10400.0, place_reset_rebuild_result=placed_state)
    log_res("rebuild: implicit price return", result, placed_state)
    log_res("rebuild: implicit price read_btc_grid", calls["read_btc_grid"], 1)
    log_res("rebuild: implicit price place_reset_rebuild arg", calls["place_reset_rebuild_args"], 10400.0)

    result, calls = run_rebuild_case(orders_with_oid, cleanup_result=True, rebuild_price_input=10000.0, computed_price=9999.0, place_reset_rebuild_result=placed_state)
    log_res("rebuild: with old orders cleanup", result, placed_state)
    log_res("rebuild: with old orders cleanup count", calls["cleanup_orders"], 1)
    log_res("rebuild: with old orders cleanup args", calls["cleanup_orders_args"], orders_with_oid)

    result, calls = run_rebuild_case(orders_with_oid, cleanup_result=False, rebuild_price_input=10000.0, computed_price=9999.0, place_reset_rebuild_result=placed_state)
    log_res("rebuild: cleanup fail -> None", result, None)
    log_res("rebuild: cleanup fail no place_reset_rebuild", calls["place_reset_rebuild"], 0)
    log_res("rebuild: cleanup fail no read_btc_grid", calls["read_btc_grid"], 0)


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

    result, calls = run_place_reset_rebuild_case(
        buy_status="ok",
        sell_status="ok",
        allow_buy_only=True,
        allow_sell_only=True,
        price=10000.0,
    )
    log_res(
        "place_reset_rebuild: both ok -> PAIR",
        result,
        {"mode": PAIR_MODE, "buy_price": 9800.0, "sell_price": 10200.0, "reference_price": 10000.0},
    )
    log_res("place_reset_rebuild: both ok no cleanup", calls["cleanup_orders"], 0)
    log_res("place_reset_rebuild: both ok call order", calls["place_limit_order_args"], ["BUY", "SELL"])

    result, calls = run_place_reset_rebuild_case(
        buy_status="ok",
        sell_status="insufficient_spot_balance",
        allow_buy_only=True,
        allow_sell_only=True,
        price=10000.0,
    )
    log_res(
        "place_reset_rebuild: buy ok -> BUY_ONLY",
        result,
        {"mode": BUY_ONLY_MODE, "buy_price": 9800.0, "reference_price": 10000.0},
    )
    log_res("place_reset_rebuild: BUY_ONLY no cleanup", calls["cleanup_orders"], 0)

    result, calls = run_place_reset_rebuild_case(
        buy_status="insufficient_spot_balance",
        sell_status="ok",
        allow_buy_only=True,
        allow_sell_only=True,
        price=10000.0,
    )
    log_res(
        "place_reset_rebuild: sell ok -> SELL_ONLY",
        result,
        {"mode": SELL_ONLY_MODE, "sell_price": 10200.0, "reference_price": 10000.0},
    )
    log_res("place_reset_rebuild: SELL_ONLY no cleanup", calls["cleanup_orders"], 0)

    result, calls = run_place_reset_rebuild_case(
        buy_status="ok",
        sell_status="insufficient_spot_balance",
        allow_buy_only=False,
        allow_sell_only=True,
        price=10000.0,
    )
    log_res("place_reset_rebuild: BUY_ONLY disallowed -> None", result, None)
    log_res("place_reset_rebuild: BUY_ONLY disallowed cleanup", calls["cleanup_orders"], 1)

    result, calls = run_place_reset_rebuild_case(
        buy_status="insufficient_spot_balance",
        sell_status="ok",
        allow_buy_only=True,
        allow_sell_only=False,
        price=10000.0,
    )
    log_res("place_reset_rebuild: SELL_ONLY disallowed -> None", result, None)
    log_res("place_reset_rebuild: SELL_ONLY disallowed cleanup", calls["cleanup_orders"], 1)

    result, calls = run_place_reset_rebuild_case(
        buy_status="error",
        sell_status="ok",
        allow_buy_only=True,
        allow_sell_only=True,
        price=10000.0,
    )
    log_res("place_reset_rebuild: buy error -> None", result, None)
    log_res("place_reset_rebuild: buy error cleanup", calls["cleanup_orders"], 1)

    result, calls = run_place_reset_rebuild_case(
        buy_status="ok",
        sell_status="error",
        allow_buy_only=True,
        allow_sell_only=True,
        price=10000.0,
    )
    log_res("place_reset_rebuild: sell error -> None", result, None)
    log_res("place_reset_rebuild: sell error cleanup", calls["cleanup_orders"], 1)

    result, calls = run_place_reset_rebuild_case(
        buy_status="error",
        sell_status="error",
        allow_buy_only=True,
        allow_sell_only=True,
        price=10000.0,
    )
    log_res("place_reset_rebuild: both error -> None", result, None)
    log_res("place_reset_rebuild: both error cleanup", calls["cleanup_orders"], 1)


def run_gateway_eval():
    print("\n🚀 gateway Step 5")

    if grid_gate is None:
        log_note("gateway: import grid_gateway", "SKIPPED", "grid_gateway import failed")
        return

    normalized_buy = grid_gate.normalize_order({"side": "B", "limitPx": 9800.4, "oid": 11})
    log_res("gateway: normalize BUY side", normalized_buy["side"], "BUY")
    log_res("gateway: normalize BUY price", normalized_buy["price"], grid_gate.normalize_price(9800.4))
    log_res("gateway: normalize BUY oid keep", normalized_buy["oid"], 11)

    normalized_sell = grid_gate.normalize_order({"side": "A", "limitPx": 10200.4, "oid": 22})
    log_res("gateway: normalize SELL side", normalized_sell["side"], "SELL")
    log_res("gateway: normalize SELL price", normalized_sell["price"], grid_gate.normalize_price(10200.4))

    try:
        grid_gate.normalize_order({"side": "X", "limitPx": 10000.0})
        log_res("gateway: normalize invalid side", "NO_ERROR", "ValueError")
    except ValueError:
        log_res("gateway: normalize invalid side", "ValueError", "ValueError")

    raw_orders = [
        {"side": "B", "limitPx": 9800.4, "oid": 11},
        {"side": "A", "limitPx": 10200.4, "oid": 22},
    ]
    result, calls = run_gateway_open_orders_case(raw_orders)
    log_res(
        "gateway: read_orders result",
        result,
        [
            {"side": "BUY", "price": grid_gate.normalize_price(9800.4), "oid": 11, "limitPx": 9800.4},
            {"side": "SELL", "price": grid_gate.normalize_price(10200.4), "oid": 22, "limitPx": 10200.4},
        ],
    )
    log_res("gateway: read_orders retry call", calls["retry_read"], 1)

    result = run_gateway_read_mid_case(mids_value={grid_gate.BTC_MID_KEY: 10123.4})
    log_res("gateway: read_btc_mid success", result, grid_gate.normalize_price(10123.4))

    result = run_gateway_read_mid_case(mids_value={grid_gate.BTC_MID_KEY: 0})
    log_res("gateway: read_btc_mid zero -> None", result, None)

    result = run_gateway_read_mid_case(mids_value={})
    log_res("gateway: read_btc_mid missing -> None", result, None)

    result = run_gateway_read_mid_case(mids_exc=RuntimeError("mid failed"))
    log_res("gateway: read_btc_mid exc", result, "RuntimeError")

    result, calls = run_gateway_reference_case(mids_value={grid_gate.BTC_MID_KEY: 10123.4})
    expected_reference = grid_gate.normalize_price(int((10123.4 / GRID_STEP) + 0.5) * GRID_STEP)
    log_res("gateway: read_btc_grid success", result, expected_reference)
    log_res("gateway: read_btc_grid read call", calls["all_mids"], 1)

    result, _ = run_gateway_reference_case(mids_value={grid_gate.BTC_MID_KEY: 0})
    log_res("gateway: read_btc_grid zero", result, "ValueError")

    result, _ = run_gateway_reference_case(mids_value={})
    log_res("gateway: read_btc_grid missing", result, "ValueError")

    result, _ = run_gateway_reference_case(mids_value={grid_gate.BTC_MID_KEY: 100.0})
    log_res("gateway: read_btc_grid small mid", result, grid_gate.normalize_price(GRID_STEP))

    result, _ = run_retry_read_case([TimeoutError("timeout"), True], retries=1)
    log_res("gateway: retryable TimeoutError", result, True)

    result, _ = run_retry_read_case([ConnectionResetError("connection reset"), True], retries=1)
    log_res("gateway: retryable reset", result, True)

    result, _ = run_retry_read_case([OSError("temporarily unavailable"), True], retries=1)
    log_res("gateway: retryable OSError text", result, True)

    result, _ = run_retry_read_case([ValueError("x"), True], retries=1)
    log_res("gateway: non-retryable ValueError", result, "ValueError")

def run_bootstrap_step7a_eval():
    print("\n🚀 bootstrap Step 7A")

    if grid_engine is None:
        log_note("bootstrap step7A: import grid", "SKIPPED", "grid import failed")
        return

    inherited_state = {"mode": PAIR_MODE, "buy_price": 9800.0, "sell_price": 10200.0}
    rebuilt_state = {"mode": PAIR_MODE, "buy_price": 9700.0, "sell_price": 10100.0}
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
    log_res("bootstrap_saved_state: reject->rebuild args", calls["rebuild_args"][0], live_snapshot(orders[:1]))

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
        buy_action = {"side": "BUY", "price": 9800.0, "size": 0.00123}
        sell_action = {"side": "SELL", "price": 10200.0, "size": 0.00456}

        ok_result = {"response": {"data": {"statuses": [{"resting": {"oid": 1}}]}}}

        result, calls = run_place_limit_order_case(buy_action, ok_result)
        log_res("place_limit_order: BUY result", result, "ok")
        log_res("place_limit_order: BUY call count", calls["trader_order"], 1)
        log_res(
            "place_limit_order: BUY payload",
            calls["trader_order_args"],
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
            calls["trader_order_args"],
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

        result, calls = run_retry_read_case(
            [retry_exc, retry_exc, {"ok": 1}],
            retries=2,
        )
        log_res("retry_read: retry then success", result, {"ok": 1})
        log_res("retry_read: retry then success calls", calls["read_func"], 3)
        log_res("retry_read: retry then success sleeps", calls["sleep"], 2)
        log_res("retry_read: retry then success delay", calls["sleep_args"], [0.1, 0.1])

        result, calls = run_retry_read_case(
            [ValueError("bad request")],
            retries=2,
        )
        log_res("retry_read: non-retryable raise", result, "ValueError")
        log_res("retry_read: non-retryable calls", calls["read_func"], 1)
        log_res("retry_read: non-retryable no sleep", calls["sleep"], 0)

        result, calls = run_retry_read_case(
            [retry_exc, retry_exc, retry_exc],
            retries=2,
        )
        log_res("retry_read: exhausted raise", result, "TimeoutError")
        log_res("retry_read: exhausted calls", calls["read_func"], 3)
        log_res("retry_read: exhausted sleeps", calls["sleep"], 2)
        log_res("retry_read: exhausted delay", calls["sleep_args"], [0.1, 0.1])


if __name__ == "__main__":
    run_order_mode_classification_eval()
    run_pair_mode_eval()
    run_buy_only_eval()
    run_sell_only_eval()
    run_run_cycle_eval()
    run_execution_eval()
    run_execution_step4_eval()
    run_gateway_eval()
    run_bootstrap_step7a_eval()
    run_gateway_execution_step7b_eval()
    run_red_team_eval()
    print_final_summary()
    print("\n🏁 Test Cycle Complete.")
 
