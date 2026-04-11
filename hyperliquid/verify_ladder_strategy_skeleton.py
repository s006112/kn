"""Deterministic offline verification for the Stage 4 ladder skeleton."""

from dataclasses import dataclass

from grid_strategy_ladder import (
    build_expected_ladder_structure,
    compare_live_ladder_to_expected,
    decide_ladder_action,
    parse_live_ladder_snapshot,
)


@dataclass(frozen=True)
class VerificationCase:
    name: str
    saved_ladder_state: dict
    orders: list
    expected_action: tuple
    expected_relation: dict
    expected_missing: dict
    expected_extra: dict


def order(side, price):
    return {"side": side, "limitPx": float(price)}


def relation_flags(compare_result):
    relation = compare_result["relation"]
    return {
        "exact": relation["is_exact_keep_candidate"],
        "fill": relation["is_fill_transition_candidate"],
        "stale": relation["is_stale_candidate"],
        "abnormal": relation["is_abnormal_candidate"],
    }


def make_cases():
    depth_two = build_expected_ladder_structure(
        reference_price=100.0,
        grid_step=10.0,
        buy_grid_factor=1.0,
        sell_grid_factor=1.0,
        depth_per_side=2,
    )
    depth_one = build_expected_ladder_structure(
        reference_price=100.0,
        grid_step=10.0,
        buy_grid_factor=1.0,
        sell_grid_factor=1.0,
        depth_per_side=1,
    )
    depth_three = build_expected_ladder_structure(
        reference_price=100.0,
        grid_step=5.0,
        buy_grid_factor=2.0,
        sell_grid_factor=1.5,
        depth_per_side=3,
    )

    return [
        VerificationCase(
            name="exact_keep",
            saved_ladder_state=depth_two,
            orders=[
                order("B", 90.0),
                order("B", 80.0),
                order("A", 110.0),
                order("A", 120.0),
            ],
            expected_action=("keep", None),
            expected_relation={"exact": True, "fill": False, "stale": False, "abnormal": False},
            expected_missing={"buy_levels": [], "sell_levels": []},
            expected_extra={"buy_levels": [], "sell_levels": []},
        ),
        VerificationCase(
            name="fill_missing_sell_levels",
            saved_ladder_state=depth_two,
            orders=[
                order("B", 90.0),
                order("B", 80.0),
                order("A", 110.0),
            ],
            expected_action=("rebuild", None),
            expected_relation={"exact": False, "fill": True, "stale": False, "abnormal": False},
            expected_missing={"buy_levels": [], "sell_levels": [120.0]},
            expected_extra={"buy_levels": [], "sell_levels": []},
        ),
        VerificationCase(
            name="fill_missing_buy_levels",
            saved_ladder_state=depth_two,
            orders=[
                order("B", 90.0),
                order("A", 110.0),
                order("A", 120.0),
            ],
            expected_action=("rebuild", None),
            expected_relation={"exact": False, "fill": True, "stale": False, "abnormal": False},
            expected_missing={"buy_levels": [80.0], "sell_levels": []},
            expected_extra={"buy_levels": [], "sell_levels": []},
        ),
        VerificationCase(
            name="stale_drift_shifted_full_ladder",
            saved_ladder_state=depth_two,
            orders=[
                order("B", 91.0),
                order("B", 81.0),
                order("A", 111.0),
                order("A", 121.0),
            ],
            expected_action=("rebuild", None),
            expected_relation={"exact": False, "fill": False, "stale": True, "abnormal": False},
            expected_missing={"buy_levels": [90.0, 80.0], "sell_levels": [110.0, 120.0]},
            expected_extra={"buy_levels": [91.0, 81.0], "sell_levels": [111.0, 121.0]},
        ),
        VerificationCase(
            name="abnormal_extra_unexpected_levels",
            saved_ladder_state=depth_two,
            orders=[
                order("B", 90.0),
                order("B", 80.0),
                order("A", 110.0),
                order("A", 120.0),
                order("A", 130.0),
            ],
            expected_action=("abnormal", None),
            expected_relation={"exact": False, "fill": False, "stale": False, "abnormal": True},
            expected_missing={"buy_levels": [], "sell_levels": []},
            expected_extra={"buy_levels": [], "sell_levels": [130.0]},
        ),
        VerificationCase(
            name="abnormal_uninterpretable_family",
            saved_ladder_state=depth_two,
            orders=[
                order("B", 90.0),
                order("B", 80.0),
            ],
            expected_action=("abnormal", None),
            expected_relation={"exact": False, "fill": False, "stale": False, "abnormal": True},
            expected_missing={"buy_levels": [], "sell_levels": [110.0, 120.0]},
            expected_extra={"buy_levels": [], "sell_levels": []},
        ),
        VerificationCase(
            name="depth_per_side_1_sanity",
            saved_ladder_state=depth_one,
            orders=[
                order("B", 90.0),
                order("A", 110.0),
            ],
            expected_action=("keep", None),
            expected_relation={"exact": True, "fill": False, "stale": False, "abnormal": False},
            expected_missing={"buy_levels": [], "sell_levels": []},
            expected_extra={"buy_levels": [], "sell_levels": []},
        ),
        VerificationCase(
            name="depth_per_side_3_sanity",
            saved_ladder_state=depth_three,
            orders=[
                order("B", 90.0),
                order("B", 80.0),
                order("B", 70.0),
                order("A", 107.5),
                order("A", 115.0),
                order("A", 122.5),
            ],
            expected_action=("keep", None),
            expected_relation={"exact": True, "fill": False, "stale": False, "abnormal": False},
            expected_missing={"buy_levels": [], "sell_levels": []},
            expected_extra={"buy_levels": [], "sell_levels": []},
        ),
    ]


def verify_case(case):
    live_snapshot = parse_live_ladder_snapshot(case.orders)
    compare_result = compare_live_ladder_to_expected(
        case.saved_ladder_state,
        live_snapshot,
    )
    action = decide_ladder_action(compare_result, case.saved_ladder_state)

    if action != case.expected_action:
        raise AssertionError(
            f"{case.name}: expected action {case.expected_action}, got {action}"
        )

    if relation_flags(compare_result) != case.expected_relation:
        raise AssertionError(
            f"{case.name}: expected relation {case.expected_relation}, "
            f"got {relation_flags(compare_result)}"
        )

    if compare_result["missing"] != case.expected_missing:
        raise AssertionError(
            f"{case.name}: expected missing {case.expected_missing}, "
            f"got {compare_result['missing']}"
        )

    if compare_result["extra"] != case.expected_extra:
        raise AssertionError(
            f"{case.name}: expected extra {case.expected_extra}, "
            f"got {compare_result['extra']}"
        )

    depth = case.saved_ladder_state["parameters"]["depth_per_side"]
    summary = compare_result["summary"]
    print(
        f"[ok] {case.name}: depth={depth} shape={live_snapshot['shape_hint']} "
        f"action={action} missing=({summary['filled_buy_count']},{summary['filled_sell_count']})"
    )


def main():
    for case in make_cases():
        verify_case(case)
    print("All ladder skeleton checks passed.")


if __name__ == "__main__":
    main()
