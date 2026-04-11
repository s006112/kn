"""Offline verification for conservative Stage 6 pair absorption.

This script proves the current Step 6 status split:
- some saved `PAIR` paths must be ladder-backed
- residual paths must remain pair-specific fallback
- engine action tuples must remain unchanged
"""

from dataclasses import dataclass

from grid_strategy import (
    compare_pair_live_vs_expected,
    decide_pair_action_from_compare,
    parse_pair_live_structure,
)


PAIR_MODE = "PAIR"
BUY_ONLY_MODE = "BUY_ONLY"
SELL_ONLY_MODE = "SELL_ONLY"
ABNORMAL_MODE = "ABNORMAL"

GRID_STEP = 10.0
BUY_GRID_FACTOR = 1.0
SELL_GRID_FACTOR = 1.0
REANCHOR_BREAK = True
REANCHOR_BREAK_STEPS = 2
BTC_MID_KEY = "BTC"

ABSORBED_CASES = {
    "pair_keep",
    "pair_to_buy_only",
    "pair_to_sell_only",
    "abnormal_structure",
}

FALLBACK_CASES = {
    "buy_only_completion",
    "sell_only_completion",
    "buy_only_keep",
    "buy_only_stale",
    "sell_only_keep",
}


@dataclass(frozen=True)
class VerificationCase:
    name: str
    state: dict
    orders: list
    btc_mid: float
    expected_action: tuple
    expected_absorption: dict


class StubInfo:
    def __init__(self, btc_mid):
        self._btc_mid = btc_mid

    def all_mids(self):
        return {BTC_MID_KEY: str(self._btc_mid)}


def order(side, price):
    return {"side": side, "limitPx": float(price)}


def make_state(mode, reference_price=100.0):
    return {
        "mode": mode,
        "buy_price": 90.0,
        "sell_price": 110.0,
        "reference_price": float(reference_price),
    }


def quiet_log(*_args, **_kwargs):
    return None


def quiet_keep_log(*_args, **_kwargs):
    return None


def format_price(value):
    if value is None:
        return "None"
    return f"{value:.1f}"


def assert_valid_engine_action(action):
    if not isinstance(action, tuple) or len(action) != 2:
        raise AssertionError(f"invalid action tuple: {action}")

    action_name, reference_price = action
    if action_name == "keep":
        if reference_price is not None:
            raise AssertionError(f"keep must not carry reference: {action}")
        return

    if action_name == "rebuild":
        if reference_price is not None and not isinstance(reference_price, (int, float)):
            raise AssertionError(f"rebuild reference must be numeric or None: {action}")
        return

    if action_name == "abnormal":
        if reference_price is not None:
            raise AssertionError(f"abnormal must not carry reference: {action}")
        return

    raise AssertionError(f"unexpected action kind: {action}")


def assert_absorption_boundary(case_name, absorption):
    if case_name in ABSORBED_CASES:
        if not absorption["used_ladder_m1"]:
            raise AssertionError(f"{case_name}: expected ladder-backed path")
        if absorption["used_pair_fallback"]:
            raise AssertionError(f"{case_name}: should not use pair fallback")
        return

    if case_name in FALLBACK_CASES:
        if absorption["used_ladder_m1"]:
            raise AssertionError(f"{case_name}: should not be over-absorbed")
        if not absorption["used_pair_fallback"]:
            raise AssertionError(f"{case_name}: expected pair-specific fallback")
        return

    raise AssertionError(f"{case_name}: case is not assigned to a Step 6 group")


def make_cases():
    return [
        VerificationCase(
            name="pair_keep",
            state=make_state(PAIR_MODE),
            orders=[order("B", 90.0), order("A", 110.0)],
            btc_mid=100.0,
            expected_action=("keep", None),
            expected_absorption={
                "used_ladder_m1": True,
                "used_pair_fallback": False,
                "path": "pair_keep",
            },
        ),
        VerificationCase(
            name="pair_to_buy_only",
            state=make_state(PAIR_MODE),
            orders=[order("B", 90.0)],
            btc_mid=100.0,
            expected_action=("rebuild", 110.0),
            expected_absorption={
                "used_ladder_m1": True,
                "used_pair_fallback": False,
                "path": "pair_to_buy_only_fill",
            },
        ),
        VerificationCase(
            name="pair_to_sell_only",
            state=make_state(PAIR_MODE),
            orders=[order("A", 110.0)],
            btc_mid=100.0,
            expected_action=("rebuild", 90.0),
            expected_absorption={
                "used_ladder_m1": True,
                "used_pair_fallback": False,
                "path": "pair_to_sell_only_fill",
            },
        ),
        VerificationCase(
            name="buy_only_completion",
            state=make_state(BUY_ONLY_MODE),
            orders=[],
            btc_mid=100.0,
            expected_action=("rebuild", 90.0),
            expected_absorption={
                "used_ladder_m1": False,
                "used_pair_fallback": True,
                "path": "buy_only_completion",
            },
        ),
        VerificationCase(
            name="sell_only_completion",
            state=make_state(SELL_ONLY_MODE),
            orders=[],
            btc_mid=100.0,
            expected_action=("rebuild", 110.0),
            expected_absorption={
                "used_ladder_m1": False,
                "used_pair_fallback": True,
                "path": "sell_only_completion",
            },
        ),
        VerificationCase(
            name="buy_only_keep",
            state=make_state(BUY_ONLY_MODE),
            orders=[order("B", 90.0)],
            btc_mid=109.0,
            expected_action=("keep", None),
            expected_absorption={
                "used_ladder_m1": False,
                "used_pair_fallback": True,
                "path": "buy_only_branch",
            },
        ),
        VerificationCase(
            name="buy_only_stale",
            state=make_state(BUY_ONLY_MODE),
            orders=[order("B", 90.0)],
            btc_mid=115.0,
            expected_action=("rebuild", None),
            expected_absorption={
                "used_ladder_m1": False,
                "used_pair_fallback": True,
                "path": "buy_only_branch",
            },
        ),
        VerificationCase(
            name="sell_only_keep",
            state=make_state(SELL_ONLY_MODE),
            orders=[order("A", 110.0)],
            btc_mid=115.0,
            expected_action=("keep", None),
            expected_absorption={
                "used_ladder_m1": False,
                "used_pair_fallback": True,
                "path": "sell_only_branch",
            },
        ),
        VerificationCase(
            name="abnormal_structure",
            state=make_state(PAIR_MODE),
            orders=[order("B", 90.0), order("A", 111.0)],
            btc_mid=100.0,
            expected_action=("abnormal", None),
            expected_absorption={
                "used_ladder_m1": True,
                "used_pair_fallback": False,
                "path": "abnormal",
            },
        ),
    ]


def verify_case(case):
    live_structure = parse_pair_live_structure(
        case.orders,
        GRID_STEP,
        BUY_GRID_FACTOR,
        SELL_GRID_FACTOR,
        PAIR_MODE,
        BUY_ONLY_MODE,
        SELL_ONLY_MODE,
        ABNORMAL_MODE,
    )
    compare_result = compare_pair_live_vs_expected(
        case.state,
        live_structure,
        grid_step=GRID_STEP,
        buy_grid_factor=BUY_GRID_FACTOR,
        sell_grid_factor=SELL_GRID_FACTOR,
        pair_mode=PAIR_MODE,
        buy_only_mode=BUY_ONLY_MODE,
        sell_only_mode=SELL_ONLY_MODE,
    )
    action = decide_pair_action_from_compare(
        StubInfo(case.btc_mid),
        case.state,
        compare_result,
        buy_only_mode=BUY_ONLY_MODE,
        sell_only_mode=SELL_ONLY_MODE,
        reanchor_break=REANCHOR_BREAK,
        btc_mid_key=BTC_MID_KEY,
        grid_step=GRID_STEP,
        reanchor_break_steps=REANCHOR_BREAK_STEPS,
        log_keep_state=quiet_keep_log,
        log_msg=quiet_log,
        format_price=format_price,
    )

    if action != case.expected_action:
        raise AssertionError(
            f"{case.name}: expected action {case.expected_action}, got {action}"
        )
    assert_valid_engine_action(action)

    absorption = compare_result["absorption"]
    if absorption != case.expected_absorption:
        raise AssertionError(
            f"{case.name}: expected absorption {case.expected_absorption}, "
            f"got {absorption}"
        )
    assert_absorption_boundary(case.name, absorption)

    print(
        f"[ok] {case.name}: action={action} "
        f"ladder_m1={absorption['used_ladder_m1']} "
        f"pair_fallback={absorption['used_pair_fallback']} "
        f"path={absorption['path']}"
    )


def verify_group_partition():
    case_names = {case.name for case in make_cases()}
    if ABSORBED_CASES & FALLBACK_CASES:
        raise AssertionError("Step 6 groups must not overlap")
    if case_names != (ABSORBED_CASES | FALLBACK_CASES):
        raise AssertionError(
            "Step 6 groups must cover every representative verification case"
        )

    print(
        "[ok] grouped Step 6 status: "
        f"absorbed={sorted(ABSORBED_CASES)} "
        f"fallback={sorted(FALLBACK_CASES)}"
    )


def main():
    cases = make_cases()
    verify_group_partition()
    for case in cases:
        verify_case(case)
    print("All pair absorption checks passed.")


if __name__ == "__main__":
    main()
