"""Offline Stage 3 proof harness for pair -> ladder M=1 degeneration.

This file is intentionally:
- proof-only
- non-runtime
- non-generalized beyond depth_per_side = 1

It exercises the current single-pair pure strategy functions, maps the same
state into schema language, and checks how far an M=1 ladder interpretation can
be supported without changing runtime behavior.
"""

from dataclasses import dataclass

from grid_strategy import (
    compare_pair_live_vs_expected,
    decide_pair_action_from_compare,
    parse_pair_live_structure,
)
from grid_strategy_schema_adapter import (
    compare_pair_schema_state_to_live_structure,
    map_action_to_decision_result,
    map_orders_to_live_structure,
    map_pair_state_to_schema_state,
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

PROVEN = "PROVEN"
PARTIAL = "PARTIALLY PROVEN"
NOT_PROVEN = "NOT PROVEN"


@dataclass(frozen=True)
class ProofCase:
    """Representative single-pair case used by the Stage 3 proof harness."""

    name: str
    pair_case: str
    pair_interpretation: str
    state: dict
    orders: list
    btc_mid: float
    expected_pair_action: tuple


class StubInfo:
    """Minimal Info stub for deterministic anchor-break evaluation."""

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


def format_price(value):
    if value is None:
        return "None"
    return f"{value:.1f}"


def format_action(action):
    return f"({action[0]}, {format_price(action[1])})"


def make_cases():
    return [
        ProofCase(
            name="pair_keep",
            pair_case="PAIR keep",
            pair_interpretation="saved PAIR and live pair prices still match exactly",
            state=make_state(PAIR_MODE),
            orders=[order("B", 90.0), order("A", 110.0)],
            btc_mid=100.0,
            expected_pair_action=("keep", None),
        ),
        ProofCase(
            name="pair_to_buy_only",
            pair_case="PAIR -> BUY_ONLY",
            pair_interpretation="saved PAIR, sell filled, buy residual remains",
            state=make_state(PAIR_MODE),
            orders=[order("B", 90.0)],
            btc_mid=100.0,
            expected_pair_action=("rebuild", 110.0),
        ),
        ProofCase(
            name="pair_to_sell_only",
            pair_case="PAIR -> SELL_ONLY",
            pair_interpretation="saved PAIR, buy filled, sell residual remains",
            state=make_state(PAIR_MODE),
            orders=[order("A", 110.0)],
            btc_mid=100.0,
            expected_pair_action=("rebuild", 90.0),
        ),
        ProofCase(
            name="buy_only_completion",
            pair_case="BUY_ONLY -> no orders",
            pair_interpretation="saved BUY_ONLY residual has fully completed",
            state=make_state(BUY_ONLY_MODE),
            orders=[],
            btc_mid=100.0,
            expected_pair_action=("rebuild", 90.0),
        ),
        ProofCase(
            name="sell_only_completion",
            pair_case="SELL_ONLY -> no orders",
            pair_interpretation="saved SELL_ONLY residual has fully completed",
            state=make_state(SELL_ONLY_MODE),
            orders=[],
            btc_mid=100.0,
            expected_pair_action=("rebuild", 110.0),
        ),
        ProofCase(
            name="buy_only_keep",
            pair_case="BUY_ONLY keep",
            pair_interpretation="saved BUY_ONLY residual remains valid and not stale",
            state=make_state(BUY_ONLY_MODE),
            orders=[order("B", 90.0)],
            btc_mid=109.0,
            expected_pair_action=("keep", None),
        ),
        ProofCase(
            name="buy_only_stale",
            pair_case="BUY_ONLY stale",
            pair_interpretation="saved BUY_ONLY residual breaks anchor threshold",
            state=make_state(BUY_ONLY_MODE),
            orders=[order("B", 90.0)],
            btc_mid=115.0,
            expected_pair_action=("rebuild", None),
        ),
        ProofCase(
            name="sell_only_keep",
            pair_case="SELL_ONLY keep",
            pair_interpretation="saved SELL_ONLY residual remains the accepted single-sided state",
            state=make_state(SELL_ONLY_MODE),
            orders=[order("A", 110.0)],
            btc_mid=115.0,
            expected_pair_action=("keep", None),
        ),
        ProofCase(
            name="abnormal_structure",
            pair_case="abnormal",
            pair_interpretation="live two-sided shape exists but does not satisfy pair price spacing",
            state=make_state(PAIR_MODE),
            orders=[order("B", 90.0), order("A", 111.0)],
            btc_mid=100.0,
            expected_pair_action=("abnormal", None),
        ),
    ]


def derive_pair_case(state, live_structure, compare_result, action):
    if compare_result["relation"]["is_pair_keep_candidate"]:
        return "PAIR keep"

    fill_reason = compare_result["fill_rebuild"]["reason"]
    if fill_reason == "sell_filled":
        return "PAIR -> BUY_ONLY"
    if fill_reason == "buy_filled":
        return "PAIR -> SELL_ONLY"
    if fill_reason == "buy_only_completed":
        return "BUY_ONLY -> no orders"
    if fill_reason == "sell_only_completed":
        return "SELL_ONLY -> no orders"

    branch_mode = compare_result["relation"]["single_sided_branch_mode"]
    if branch_mode == BUY_ONLY_MODE:
        if action == ("rebuild", None):
            return "BUY_ONLY stale"
        return "BUY_ONLY keep"
    if branch_mode == SELL_ONLY_MODE:
        return "SELL_ONLY keep"

    return "abnormal"


def is_buy_residual_stale(schema_state, schema_live, btc_mid):
    """Proof-only stale split for M=1.

    This mirrors the current BUY_ONLY anchor-break rule, but it is not runtime
    ladder logic and should not be treated as a generalized ladder algorithm.
    """
    if schema_state["mode"] != BUY_ONLY_MODE:
        return False
    if schema_live["shape_hint"] != BUY_ONLY_MODE:
        return False
    if len(schema_live["buy_levels"]) != 1 or schema_live["sell_levels"]:
        return False
    if btc_mid <= 0:
        return False

    threshold_distance = (
        schema_state["parameters"]["grid_step"] * REANCHOR_BREAK_STEPS
    )
    return btc_mid - schema_live["buy_levels"][0] >= threshold_distance


def derive_proof_only_ladder_action(schema_state, schema_live, schema_compare, btc_mid):
    """Derive an M=1 ladder action for proof purposes only.

    This function is intentionally narrow:
    - proof-only
    - non-runtime
    - non-generalized beyond depth_per_side = 1

    It uses the ladder-first schema objects plus the current single-sided stale
    rule to check whether an M=1 interpretation can reproduce the pair action.
    """
    mode = schema_state["mode"]
    shape_hint = schema_live["shape_hint"]
    relation = schema_compare["relation"]
    expected_buy = schema_state["expected_structure"]["buy_levels"][0]
    expected_sell = schema_state["expected_structure"]["sell_levels"][0]

    if relation["is_exact_keep_candidate"]:
        return ("keep", None), "exact keep"

    if mode == PAIR_MODE and shape_hint == BUY_ONLY_MODE:
        return ("rebuild", expected_sell), "fill transition: PAIR -> BUY_ONLY"

    if mode == PAIR_MODE and shape_hint == SELL_ONLY_MODE:
        return ("rebuild", expected_buy), "fill transition: PAIR -> SELL_ONLY"

    if mode == BUY_ONLY_MODE and shape_hint == "EMPTY":
        return ("rebuild", expected_buy), "fill transition: BUY_ONLY -> EMPTY"

    if mode == SELL_ONLY_MODE and shape_hint == "EMPTY":
        return ("rebuild", expected_sell), "fill transition: SELL_ONLY -> EMPTY"

    if mode == BUY_ONLY_MODE and shape_hint == BUY_ONLY_MODE:
        if is_buy_residual_stale(schema_state, schema_live, btc_mid):
            return ("rebuild", None), "stale/drift rebuild"
        return ("keep", None), "buy residual keep"

    if mode == SELL_ONLY_MODE and shape_hint == SELL_ONLY_MODE:
        return ("keep", None), "sell residual keep"

    return ("abnormal", None), "abnormal"


def evaluate_level_structure(state, schema_state):
    buy_levels = schema_state["expected_structure"]["buy_levels"]
    sell_levels = schema_state["expected_structure"]["sell_levels"]
    if (
        schema_state["parameters"]["depth_per_side"] == 1
        and buy_levels == [state["buy_price"]]
        and sell_levels == [state["sell_price"]]
    ):
        return PROVEN, "schema state keeps one buy level and one sell level"
    return NOT_PROVEN, "schema state does not reduce to one level per side"


def evaluate_compare_status(case, schema_live, schema_compare):
    relation = schema_compare["relation"]
    missing = schema_compare["missing"]
    mode = case.state["mode"]
    shape_hint = schema_live["shape_hint"]

    if case.name == "pair_keep":
        if relation["is_exact_keep_candidate"]:
            return PROVEN, "exact keep: live levels equal expected levels"
        return NOT_PROVEN, "exact keep candidate was not preserved"

    if case.name == "pair_to_buy_only":
        if (
            relation["is_fill_transition_candidate"]
            and missing["sell_levels"] == [case.state["sell_price"]]
            and not missing["buy_levels"]
        ):
            return PROVEN, "fill transition: sell level missing, buy level remains"
        return NOT_PROVEN, "PAIR -> BUY_ONLY fill transition was not preserved"

    if case.name == "pair_to_sell_only":
        if (
            relation["is_fill_transition_candidate"]
            and missing["buy_levels"] == [case.state["buy_price"]]
            and not missing["sell_levels"]
        ):
            return PROVEN, "fill transition: buy level missing, sell level remains"
        return NOT_PROVEN, "PAIR -> SELL_ONLY fill transition was not preserved"

    if case.name == "buy_only_completion":
        if mode == BUY_ONLY_MODE and shape_hint == "EMPTY" and relation["is_fill_transition_candidate"]:
            return PARTIAL, (
                "saved BUY_ONLY + live EMPTY is representable as fill transition, "
                "but compare summary marks both sides missing"
            )
        return NOT_PROVEN, "BUY_ONLY completion is not representable in compare language"

    if case.name == "sell_only_completion":
        if mode == SELL_ONLY_MODE and shape_hint == "EMPTY" and relation["is_fill_transition_candidate"]:
            return PARTIAL, (
                "saved SELL_ONLY + live EMPTY is representable as fill transition, "
                "but compare summary marks both sides missing"
            )
        return NOT_PROVEN, "SELL_ONLY completion is not representable in compare language"

    if case.name == "buy_only_keep":
        if mode == BUY_ONLY_MODE and shape_hint == BUY_ONLY_MODE and relation["is_stale_candidate"]:
            return PARTIAL, (
                "BUY_ONLY residual family is representable, "
                "but compare object alone does not split keep from stale"
            )
        return NOT_PROVEN, "BUY_ONLY keep is not representable in compare language"

    if case.name == "buy_only_stale":
        if mode == BUY_ONLY_MODE and shape_hint == BUY_ONLY_MODE and relation["is_stale_candidate"]:
            return PARTIAL, (
                "BUY_ONLY stale family is representable, "
                "but stale detection depends on external mid, not compare object alone"
            )
        return NOT_PROVEN, "BUY_ONLY stale is not representable in compare language"

    if case.name == "sell_only_keep":
        if (
            mode == SELL_ONLY_MODE
            and shape_hint == SELL_ONLY_MODE
            and relation["family_match"]
            and not relation["is_fill_transition_candidate"]
            and not relation["is_abnormal_candidate"]
        ):
            return PROVEN, "single sell residual remains a legal M=1 continuation"
        return NOT_PROVEN, "SELL_ONLY keep is not representable in compare language"

    if case.name == "abnormal_structure":
        if relation["is_abnormal_candidate"]:
            return PROVEN, "abnormal structure remains outside accepted ladder family"
        return NOT_PROVEN, "abnormal fallback was not preserved"

    return NOT_PROVEN, "unhandled compare case"


def evaluate_decision_status(pair_action, ladder_action):
    if pair_action == ladder_action:
        return PROVEN, "proof-only M=1 action matches current pair action"
    return NOT_PROVEN, "proof-only M=1 action does not match current pair action"


def evaluate_reference_status(case, pair_action):
    if case.name in {"pair_keep", "buy_only_keep", "sell_only_keep"}:
        if pair_action == ("keep", None):
            return PROVEN, "keep path does not consume a rebuild reference"
        return NOT_PROVEN, "keep path changed action shape"

    if case.name == "abnormal_structure":
        if pair_action == ("abnormal", None):
            return PROVEN, "abnormal fallback preserves empty reference"
        return NOT_PROVEN, "abnormal fallback changed action shape"

    if case.name in {"pair_to_buy_only", "pair_to_sell_only"}:
        if pair_action[0] == "rebuild" and pair_action[1] is not None:
            return PARTIAL, (
                "explicit fill-driven rebuild reference is representable, "
                "but future ladder docs do not yet lock it to the filled-side saved level"
            )
        return NOT_PROVEN, "fill-driven rebuild reference was not preserved"

    if case.name in {"buy_only_completion", "sell_only_completion"}:
        if pair_action[0] == "rebuild" and pair_action[1] is not None:
            return PARTIAL, (
                "completion rebuild can reuse the saved residual-side price, "
                "but current compare summary does not isolate that side by itself"
            )
        return NOT_PROVEN, "completion rebuild reference was not preserved"

    if case.name == "buy_only_stale":
        if pair_action == ("rebuild", None):
            return PARTIAL, (
                "fresh-reference rebuild is representable, "
                "but ladder stale/drift contract does not yet require None for this case"
            )
        return NOT_PROVEN, "stale rebuild reference was not preserved"

    return NOT_PROVEN, "unhandled reference case"


def summarize_overall(statuses):
    if any(status == NOT_PROVEN for status in statuses):
        return NOT_PROVEN
    if any(status == PARTIAL for status in statuses):
        return PARTIAL
    return PROVEN


def quiet_log(*_args, **_kwargs):
    return None


def run_case(case):
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
    pair_action = decide_pair_action_from_compare(
        StubInfo(case.btc_mid),
        case.state,
        compare_result,
        buy_only_mode=BUY_ONLY_MODE,
        sell_only_mode=SELL_ONLY_MODE,
        reanchor_break=REANCHOR_BREAK,
        btc_mid_key=BTC_MID_KEY,
        grid_step=GRID_STEP,
        reanchor_break_steps=REANCHOR_BREAK_STEPS,
        log_keep_state=quiet_log,
        log_msg=quiet_log,
        format_price=format_price,
    )
    if pair_action != case.expected_pair_action:
        raise AssertionError(
            f"{case.name}: expected pair action {case.expected_pair_action}, got {pair_action}"
        )

    observed_pair_case = derive_pair_case(
        case.state,
        live_structure,
        compare_result,
        pair_action,
    )
    if observed_pair_case != case.pair_case:
        raise AssertionError(
            f"{case.name}: expected pair case {case.pair_case!r}, got {observed_pair_case!r}"
        )

    schema_state = map_pair_state_to_schema_state(
        case.state,
        GRID_STEP,
        BUY_GRID_FACTOR,
        SELL_GRID_FACTOR,
    )
    schema_live = map_orders_to_live_structure(case.orders)
    schema_compare = compare_pair_schema_state_to_live_structure(
        schema_state,
        schema_live,
    )
    ladder_action, ladder_compare = derive_proof_only_ladder_action(
        schema_state,
        schema_live,
        schema_compare,
        case.btc_mid,
    )
    decision_result = map_action_to_decision_result(pair_action)

    level_status, level_note = evaluate_level_structure(case.state, schema_state)
    compare_status, compare_note = evaluate_compare_status(
        case,
        schema_live,
        schema_compare,
    )
    decision_status, decision_note = evaluate_decision_status(
        pair_action,
        ladder_action,
    )
    reference_status, reference_note = evaluate_reference_status(
        case,
        pair_action,
    )

    return {
        "case": case,
        "pair_case": observed_pair_case,
        "pair_action": pair_action,
        "pair_compare_relation": compare_result["relation"],
        "schema_state": schema_state,
        "schema_live": schema_live,
        "schema_compare": schema_compare,
        "ladder_action": ladder_action,
        "ladder_compare": ladder_compare,
        "decision_result": decision_result,
        "level_status": level_status,
        "level_note": level_note,
        "compare_status": compare_status,
        "compare_note": compare_note,
        "decision_status": decision_status,
        "decision_note": decision_note,
        "reference_status": reference_status,
        "reference_note": reference_note,
    }


def main():
    print("Stage 3 proof harness: pair -> ladder M=1")
    print("Proof-only local M=1 model: non-runtime, non-generalized beyond depth_per_side = 1")
    print()

    results = [run_case(case) for case in make_cases()]

    for result in results:
        case = result["case"]
        schema_compare = result["schema_compare"]
        print(f"[{case.name}] {case.pair_case}")
        print(f"pair:   {case.pair_interpretation}")
        print(
            "action: "
            f"pair={format_action(result['pair_action'])} | "
            f"ladder_m1={format_action(result['ladder_action'])}"
        )
        print(
            "shape:  "
            f"live={result['schema_live']['shape_hint']} | "
            f"ladder_compare={result['ladder_compare']}"
        )
        print(
            "status: "
            f"level={result['level_status']} | "
            f"compare={result['compare_status']} | "
            f"decision={result['decision_status']} | "
            f"reference={result['reference_status']}"
        )
        print(
            "note:   "
            f"compare={result['compare_note']}; "
            f"decision={result['decision_note']}; "
            f"reference={result['reference_note']}"
        )
        print(
            "schema: "
            f"decision={result['decision_result']} | "
            f"missing_buy={schema_compare['missing']['buy_levels']} | "
            f"missing_sell={schema_compare['missing']['sell_levels']} | "
            f"summary={schema_compare['summary']}"
        )
        print()

    overall_level = summarize_overall([result["level_status"] for result in results])
    overall_compare = summarize_overall([result["compare_status"] for result in results])
    overall_decision = summarize_overall(
        [result["decision_status"] for result in results]
    )
    overall_reference = summarize_overall(
        [result["reference_status"] for result in results]
    )

    print("Overall Stage 3 dimension summary")
    print(f"- Level Structure Equivalence: {overall_level}")
    print(f"- Compare Result Equivalence: {overall_compare}")
    print(f"- Decision Result Equivalence: {overall_decision}")
    print(f"- Reference Rule Equivalence: {overall_reference}")


if __name__ == "__main__":
    main()
