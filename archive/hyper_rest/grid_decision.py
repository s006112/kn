"""Engine-side decision orchestration helpers for the grid engine."""

from grid_infra import read_current_btc_mid
from grid_logic import (
    classify_order_shape,
    get_pair_state,
    pair_matches_state,
    should_reanchor_residual_order,
)
from grid_config import (
    BUY_GRID_FACTOR,
    BUY_ONLY_MODE,
    GRID_STEP,
    PAIR_MODE,
    REANCHOR_BREAK,
    REANCHOR_BREAK_STEPS,
    SELL_GRID_FACTOR,
    SELL_ONLY_MODE,
    ABNORMAL_MODE,
    format_price,
    log_keep_state,
    log_msg,
)


def get_current_pair_state(orders):
    """作用:
    按当前 engine 配置校验并返回 live orders 的 pair state。
    """
    return get_pair_state(
        orders,
        GRID_STEP,
        BUY_GRID_FACTOR,
        SELL_GRID_FACTOR,
        PAIR_MODE,
    )


def classify_current_order_shape(orders):
    """作用:
    按当前 engine 配置分类 live orders 的形态。
    """
    return classify_order_shape(
        orders,
        GRID_STEP,
        BUY_GRID_FACTOR,
        SELL_GRID_FACTOR,
        PAIR_MODE,
        BUY_ONLY_MODE,
        SELL_ONLY_MODE,
        ABNORMAL_MODE,
    )


def resolve_fill_rebuild_action(state, orders, order_shape):
    """作用:
    判断是否发生了成交驱动的重建条件，并返回对应动作。

    输入:
    state: 至少包含 `mode`、`buy_price` 和 `sell_price` 的状态映射。该状态可能来自
    `get_pair_state()`（包含 `pair_center_price`）或 `place_pair()`（包含 `reference_price`），
    但本函数不要求这两个字段存在，也不将它们视为同一概念。
    orders: 当前挂单集合，用于检查残单是否已经完成。
    order_shape: `orders` 的当前形态分类结果。

    输出:
    当先前状态为 `PAIR` 且当前变为单边残单时，返回 `("rebuild", reference_price)`，并使用已成交那一侧此前的价格作为重建参考价。对于 `BUY_ONLY` 或 `SELL_ONLY` 状态，当当前已无挂单时，也返回 `("rebuild", reference_price)`，并使用该残单侧保存的价格。其余情况返回 `None`。
    """
    previous_mode = state["mode"]

    if previous_mode == PAIR_MODE:
        if order_shape == SELL_ONLY_MODE:
            log_msg(f"🔥 BUY filled - {format_price(state['buy_price'])}")
            return "rebuild", state["buy_price"]

        if order_shape == BUY_ONLY_MODE:
            log_msg(f"✅ SELL filled - {format_price(state['sell_price'])}")
            return "rebuild", state["sell_price"]

        return None

    if previous_mode == BUY_ONLY_MODE and len(orders) == 0:
        log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
        return "rebuild", state["buy_price"]

    if previous_mode == SELL_ONLY_MODE and len(orders) == 0:
        log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
        return "rebuild", state["sell_price"]

    return None


def validate_keep_state(orders, state, order_shape):
    """作用:
    判断当前订单是否仍满足成对状态的 keep 条件。

    输入:
    orders: 当前挂单集合。
    state: 至少包含 `mode`、`buy_price` 和 `sell_price` 的已保存状态映射。该状态可能来自
    `get_pair_state()` 并带有 `pair_center_price`，也可能来自 `place_pair()` 并带有
    `reference_price`；本函数只校验保存的买卖价格，不假定这两个字段等价。
    order_shape: `orders` 的当前形态分类结果。

    输出:
    仅在 `PAIR` 状态下可能返回 `True`。仅当 `order_shape` 为 `PAIR`、`get_pair_state()` 成功且 `pair_matches_state()` 确认两侧价格与已保存状态完全一致时返回 `True`。其余路径均返回 `False`。透传配对校验辅助函数因异常订单字段抛出的异常。
    """
    if state["mode"] == PAIR_MODE:
        if order_shape != PAIR_MODE:
            return False

        current_pair = get_current_pair_state(orders)
        if current_pair is None:
            return False
        if not pair_matches_state(current_pair, state):
            return False
        log_keep_state("pair", "contract: keep pair")
        return True

    return False


def detect_anchor_break(info, orders, state, order_shape):
    """作用:
    检测单边残单是否已经偏离 BTC 中间价到必须重建的程度。

    输入:
    info: 被残单重锚辅助函数使用的 Hyperliquid `Info` 客户端。
    orders: 当前挂单集合。
    state: 至少包含 `mode` 的已保存状态映射。该状态可以带有 `pair_center_price` 或
    `reference_price`，但本函数只根据 `mode` 和当前订单检查单边重锚，不把两者混为同一字段。
    order_shape: `orders` 的当前形态分类结果。

    输出:
    仅当重锚功能开启、保存的模式为单边、当前形态与该模式一致、当前恰好只有一笔订单且 `should_reanchor_residual_order()` 返回 `True` 时，返回 `("rebuild", None)`。其余路径返回 `None`。在辅助函数完成自身保护性检查之后，透传其因异常订单字段抛出的异常。
    """
    if not REANCHOR_BREAK:
        return None

    if state["mode"] not in (BUY_ONLY_MODE, SELL_ONLY_MODE):
        return None

    if order_shape != state["mode"]:
        return None

    if len(orders) != 1:
        return None

    if not should_reanchor_residual_order(
        orders,
        state["mode"],
        BUY_ONLY_MODE,
        SELL_ONLY_MODE,
        read_current_btc_mid(info),
        GRID_STEP,
        REANCHOR_BREAK,
        REANCHOR_BREAK_STEPS,
    ):
        return None

    log_msg("🪝 contract: anchor break")
    return "rebuild", None


def detect_single_sided_action(info, orders, state, order_shape):
    """作用:
    决定单边状态应继续保持还是触发重建。

    输入:
    info: 用于评估 anchor break 的 Hyperliquid `Info` 客户端。
    orders: 当前挂单集合。
    state: 至少包含 `mode` 的已保存状态映射，可能来自 `get_pair_state()` 的
        `pair_center_price` schema，也可能来自 `place_pair()` 的 `reference_price` schema；
        本函数不要求也不推断这两个字段等价。
    order_shape: `orders` 的当前形态分类结果。

    输出:
    - 对于 `BUY_ONLY` 模式：
    - 若未触发 anchor break，返回 `("keep", None)`
    - 若 `detect_anchor_break()` 请求重建，返回其结果
    - 对于 `SELL_ONLY` 模式：
    - 始终返回 `("keep", None)`（不执行 anchor break 检查）

    当保存模式不是单边、当前形态与保存模式不一致，或残单方向与预期方向不一致时，返回 `None`。

    边界说明:
    当前实现中，anchor break 仅适用于 `BUY_ONLY` 残单；
    `SELL_ONLY` 残单不会触发 anchor break。
    """
    if state["mode"] == BUY_ONLY_MODE:
        if order_shape != BUY_ONLY_MODE:
            return None
        if len(orders) != 1 or orders[0]["side"] != "B":
            return None

        anchor_break_action = detect_anchor_break(info, orders, state, order_shape)
        if anchor_break_action is not None:
            return anchor_break_action

        log_keep_state("buy-only", "contract: keep buy-only")
        return "keep", None

    if state["mode"] == SELL_ONLY_MODE:
        if order_shape != SELL_ONLY_MODE:
            return None
        if len(orders) != 1 or orders[0]["side"] == "B":
            return None

        log_keep_state("sell-only", "contract: keep sell-only")
        return "keep", None

    return None


def get_loop_action(info, orders, state):
    """
    作用:
    根据当前 open orders 与上一轮已接受的 state，
    判定这一轮主循环应执行的唯一动作。

    输入:
    info: 用于订单状态判断和潜在重建决策的 Hyperliquid `Info` 客户端。
    orders: 当前挂单集合。
    state: 上一轮已接受的状态映射，可能来自 `get_pair_state()` 并包含
    `pair_center_price`，也可能来自 `place_pair()` 并包含 `reference_price`；
    本函数及其调用链不会将这两个字段名或语义统一。

    判定顺序:
    1. 先分类当前订单形状
    2. 优先检查是否命中 fill-driven rebuild
    3. 再检查 PAIR_MODE 是否满足 keep 条件
    4. 再检查 BUY_ONLY / SELL_ONLY 是否应 keep 或触发 anchor break
    5. 若以上都不满足，则判定为 abnormal

    返回:
    - ("keep", None)
    - ("rebuild", reference_price 或 None)
    - ("abnormal", None)

    边界:
    本函数只负责决策，不负责下单、撤单或实际 rebuild 执行。
    """
    order_shape = classify_current_order_shape(orders)

    fill_driven_action = resolve_fill_rebuild_action(state, orders, order_shape)
    if fill_driven_action is not None:
        return fill_driven_action

    if validate_keep_state(orders, state, order_shape):
        return "keep", None

    single_sided_action = detect_single_sided_action(info, orders, state, order_shape)
    if single_sided_action is not None:
        return single_sided_action

    return "abnormal", None
