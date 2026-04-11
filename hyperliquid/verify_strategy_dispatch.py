"""Offline verification for the Stage 5 strategy dispatch layer."""

from grid_strategy import get_loop_action as get_pair_loop_action
from grid_strategy_dispatch import get_loop_action as get_dispatch_loop_action
from grid_strategy_ladder import build_expected_ladder_structure


PAIR_MODE = "PAIR"
BUY_ONLY_MODE = "BUY_ONLY"
SELL_ONLY_MODE = "SELL_ONLY"
ABNORMAL_MODE = "ABNORMAL"


def order(side, price):
    return {"side": side, "limitPx": float(price)}


def log_msg(message):
    _ = message


def log_keep_state(keep_type, message):
    _ = keep_type, message


def format_price(value):
    return str(value)


def assert_valid_engine_action(action):
    if not isinstance(action, tuple) or len(action) != 2:
        raise AssertionError(f"invalid action shape: {action}")

    action_name, reference_price = action
    if action_name == "keep":
        if reference_price is not None:
            raise AssertionError(f"keep must not carry a reference: {action}")
        return

    if action_name == "rebuild":
        if reference_price is not None and not isinstance(reference_price, (int, float)):
            raise AssertionError(f"rebuild reference must be numeric or None: {action}")
        return

    if action_name == "abnormal":
        if reference_price is not None:
            raise AssertionError(f"abnormal must not carry a reference: {action}")
        return

    raise AssertionError(f"unknown engine action: {action}")


def verify_pair_dispatch():
    state = {
        "mode": PAIR_MODE,
        "buy_price": 90.0,
        "sell_price": 110.0,
    }
    orders = [order("B", 90.0)]

    expected = get_pair_loop_action(
        None,
        orders,
        state,
        grid_step=10.0,
        buy_grid_factor=1.0,
        sell_grid_factor=1.0,
        pair_mode=PAIR_MODE,
        buy_only_mode=BUY_ONLY_MODE,
        sell_only_mode=SELL_ONLY_MODE,
        abnormal_mode=ABNORMAL_MODE,
        reanchor_break=True,
        btc_mid_key="BTC",
        reanchor_break_steps=2,
        log_msg=log_msg,
        log_keep_state=log_keep_state,
        format_price=format_price,
    )
    actual = get_dispatch_loop_action(
        None,
        orders,
        state,
        strategy_kind="pair",
        grid_step=10.0,
        buy_grid_factor=1.0,
        sell_grid_factor=1.0,
        pair_mode=PAIR_MODE,
        buy_only_mode=BUY_ONLY_MODE,
        sell_only_mode=SELL_ONLY_MODE,
        abnormal_mode=ABNORMAL_MODE,
        reanchor_break=True,
        btc_mid_key="BTC",
        reanchor_break_steps=2,
        log_msg=log_msg,
        log_keep_state=log_keep_state,
        format_price=format_price,
    )

    if actual != expected:
        raise AssertionError(f"pair dispatch mismatch: expected {expected}, got {actual}")
    assert_valid_engine_action(actual)
    print(f"[ok] pair dispatch: {actual}")


def verify_ladder_dispatch():
    state = build_expected_ladder_structure(
        reference_price=100.0,
        grid_step=10.0,
        buy_grid_factor=1.0,
        sell_grid_factor=1.0,
        depth_per_side=2,
    )
    orders = [
        order("B", 90.0),
        order("B", 80.0),
        order("A", 110.0),
    ]

    action = get_dispatch_loop_action(
        None,
        orders,
        state,
        strategy_kind="ladder",
        grid_step=10.0,
        buy_grid_factor=1.0,
        sell_grid_factor=1.0,
        pair_mode=PAIR_MODE,
        buy_only_mode=BUY_ONLY_MODE,
        sell_only_mode=SELL_ONLY_MODE,
        abnormal_mode=ABNORMAL_MODE,
        reanchor_break=True,
        btc_mid_key="BTC",
        reanchor_break_steps=2,
        log_msg=log_msg,
        log_keep_state=log_keep_state,
        format_price=format_price,
    )

    if action != ("rebuild", None):
        raise AssertionError(f"ladder dispatch mismatch: got {action}")
    assert_valid_engine_action(action)
    print(f"[ok] ladder dispatch: {action}")


def verify_unsupported_strategy_kind():
    action = get_dispatch_loop_action(
        None,
        [],
        {},
        strategy_kind="unsupported",
        grid_step=10.0,
        buy_grid_factor=1.0,
        sell_grid_factor=1.0,
        pair_mode=PAIR_MODE,
        buy_only_mode=BUY_ONLY_MODE,
        sell_only_mode=SELL_ONLY_MODE,
        abnormal_mode=ABNORMAL_MODE,
        reanchor_break=True,
        btc_mid_key="BTC",
        reanchor_break_steps=2,
        log_msg=log_msg,
        log_keep_state=log_keep_state,
        format_price=format_price,
    )

    if action != ("abnormal", None):
        raise AssertionError(f"unsupported strategy kind mismatch: got {action}")
    assert_valid_engine_action(action)
    print(f"[ok] unsupported strategy kind: {action}")


def main():
    verify_pair_dispatch()
    verify_ladder_dispatch()
    verify_unsupported_strategy_kind()
    print("All strategy dispatch checks passed.")


if __name__ == "__main__":
    main()
