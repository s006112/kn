"""Engine-side decision orchestration helpers for the grid engine."""
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


def count_order_sides(orders):
    """作用:
    统计买侧订单和非买侧订单数量。

    输入:
    orders: 可迭代的订单映射对象，包含 `side` 字段。

    输出:
    返回 `(buy_count, sell_count)` 元组，其中 `side == "B"` 时增加 `buy_count`，其余 side 值增加 `sell_count`。
    """
    buy_count = 0
    sell_count = 0

    for order in orders:
        if order["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    return buy_count, sell_count


def get_pair_state(
    orders,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_mode,
):
    """作用:
    校验给定订单是否构成单个网格配对，并在成立时构造对应状态快照。

    输入:
    orders: 可迭代的订单映射对象，包含 `side` 和 `limitPx` 字段。
    grid_step: 用于计算配对价格预期价差的价格步长。
    buy_grid_factor: 应用于 `grid_step` 的买侧倍数。
    sell_grid_factor: 应用于 `grid_step` 的卖侧倍数。
    pair_mode: 写入返回状态映射中的模式值。

    输出:
    当且仅当恰好存在一个买单、一个非买单、两边价格均有效，且观察到的价差与
    `(buy_grid_factor + sell_grid_factor) * grid_step` 严格相等时，
    返回包含 `mode`、`buy_price`、`sell_price` 和 `pair_center_price` 的映射。
    其中 `pair_center_price` 仅表示基于当前这组已存在买卖挂单快照推导出的中点价格，
    不是市场锚点，不是重建参考价，也不是更新后的市场参考价；否则返回 `None`。
    透传异常订单字段导致的取值和浮点转换异常。
    """
    buy_price = None
    sell_price = None

    for order in orders:
        if order["side"] == "B":
            if buy_price is not None:
                return None
            buy_price = float(order["limitPx"])
        else:
            if sell_price is not None:
                return None
            sell_price = float(order["limitPx"])

    if buy_price is None or sell_price is None:
        return None

    pair_center_price = (buy_price + sell_price) / 2.0

    if pair_center_price <= 0:
        return None
    if buy_price <= 0 or buy_price >= sell_price:
        return None

    expected_gap = (buy_grid_factor + sell_grid_factor) * grid_step
    if (sell_price - buy_price) != expected_gap:
        return None

    # Snapshot midpoint from the existing pair, not a market/rebuild anchor.
    return {
        "mode": pair_mode,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "pair_center_price": pair_center_price,
    }


def classify_order_shape(
    orders,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_mode,
    buy_only_mode,
    sell_only_mode,
    abnormal_mode,
):
    """作用:
    将当前挂单分类为有效配对、单边残单或异常状态。

    输入:
    orders: 可迭代的订单映射对象，包含 `side`，在配对校验场景下还需要 `limitPx` 字段。
    grid_step: 校验配对价差时使用的价格步长。
    buy_grid_factor: 应用于 `grid_step` 的买侧倍数。
    sell_grid_factor: 应用于 `grid_step` 的卖侧倍数。
    pair_mode: 有效配对形态对应的返回值。
    buy_only_mode: 恰好一个买单且没有卖单时的返回值。
    sell_only_mode: 恰好一个卖单且没有买单时的返回值。
    abnormal_mode: 其余所有形态的返回值，包括无效的双边配对。

    输出:
    根据 `count_order_sides()` 的结果决定返回提供的模式值之一；在一买一卖的情况下，会进一步调用 `get_pair_state()` 进行判定。透传配对校验在异常订单字段下抛出的异常。
    """
    buy_count, sell_count = count_order_sides(orders)

    if buy_count == 1 and sell_count == 1:
        pair_state = get_pair_state(
            orders,
            grid_step,
            buy_grid_factor,
            sell_grid_factor,
            pair_mode,
        )
        if pair_state is not None:
            return pair_mode
        return abnormal_mode

    if buy_count == 1 and sell_count == 0:
        return buy_only_mode

    if buy_count == 0 and sell_count == 1:
        return sell_only_mode

    return abnormal_mode


def pair_matches_state(current_pair, state):
    """作用:
    检查当前配对的买卖价格是否与已保存状态严格一致。

    输入:
    current_pair: 包含 `buy_price` 和 `sell_price` 的映射。
    state: 包含 `buy_price` 和 `sell_price` 的映射。

    输出:
    当买卖两侧价格都与已保存状态完全相等时返回 `True`；否则返回 `False`。
    """
    if current_pair["buy_price"] != state["buy_price"]:
        return False
    if current_pair["sell_price"] != state["sell_price"]:
        return False
    return True


def should_reanchor_residual_order(
    orders,
    btc_mid,
    grid_step,
    reanchor_break,
    reanchor_break_steps,
):
    """作用:
    判断在 BTC 中间价远离买侧残单后，该订单是否需要重锚。

    输入:
    orders: 序列类型的订单映射对象，预期包含 `side` 和 `limitPx` 字段。
    btc_mid: 调用方已读取并解析的 BTC 中间价。
    grid_step: 与 `reanchor_break_steps` 相乘后形成阈值距离的价格步长。
    reanchor_break: 是否启用残单重锚检查的开关。
    reanchor_break_steps: 触发重锚所需的网格步数。

    输出:
    仅当重锚功能开启、恰好存在一笔买侧残单、传入的 BTC 中间价为正，并且中间价与残单价格的距离至少达到 `reanchor_break_steps * grid_step` 时返回 `True`。对于关闭的检查、非法订单数量、非买单残单或非正中间价，返回 `False`。在通过中间价保护性检查后，透传异常订单字段导致的取值和浮点转换异常。
    """
    if not reanchor_break:
        return False

    if len(orders) != 1:
        return False

    if btc_mid is None or btc_mid <= 0:
        return False

    order = orders[0]
    if order["side"] != "B":
        return False

    threshold_distance = reanchor_break_steps * grid_step
    order_price = float(order["limitPx"])
    return btc_mid - order_price >= threshold_distance


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


def detect_anchor_break(current_btc_mid, orders, state, order_shape):
    """作用:
    检测买侧残单是否已经偏离 BTC 中间价到必须重建的程度。

    输入:
    current_btc_mid: 由 gateway 预先读取并传入的当前 BTC 中间价；读取失败时可为 `None`。
    orders: 当前挂单集合。
    state: 至少包含 `mode` 的已保存状态映射。该状态可以带有 `pair_center_price` 或
    `reference_price`，但本函数只根据 `BUY_ONLY` 模式和当前订单检查买侧重锚，不把两者混为同一字段。
    order_shape: `orders` 的当前形态分类结果。

    输出:
    仅当重锚功能开启、保存的模式为 `BUY_ONLY`、当前形态为 `BUY_ONLY`、当前恰好只有一笔订单且 `should_reanchor_residual_order()` 返回 `True` 时，返回 `("rebuild", None)`。其余路径返回 `None`。在辅助函数完成自身保护性检查之后，透传其因异常订单字段抛出的异常。
    """
    if not REANCHOR_BREAK:
        return None

    if state["mode"] != BUY_ONLY_MODE:
        return None

    if order_shape != BUY_ONLY_MODE:
        return None

    if len(orders) != 1:
        return None

    if not should_reanchor_residual_order(
        orders,
        current_btc_mid,
        GRID_STEP,
        REANCHOR_BREAK,
        REANCHOR_BREAK_STEPS,
    ):
        return None

    log_msg("🪝 contract: anchor break")
    return "rebuild", None


def detect_single_sided_action(current_btc_mid, orders, state, order_shape):
    """作用:
    决定单边状态应继续保持还是触发重建。

    输入:
    current_btc_mid: 由 gateway 预先读取并传入、供 anchor break 评估使用的 BTC 中间价。
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

        anchor_break_action = detect_anchor_break(
            current_btc_mid,
            orders,
            state,
            order_shape,
        )
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


def get_loop_action(orders, state, current_btc_mid=None):
    """
    作用:
    根据当前 open orders 与上一轮已接受的 state，
    判定这一轮主循环应执行的唯一动作。

    输入:
    orders: 当前挂单集合。
    state: 上一轮已接受的状态映射，可能来自 `get_pair_state()` 并包含
    `pair_center_price`，也可能来自 `place_pair()` 并包含 `reference_price`；
    本函数及其调用链不会将这两个字段名或语义统一。
    current_btc_mid: 由 gateway 预先提供的 BTC 中间价，仅在单边 anchor break 判定时使用；
    若读取失败或当前路径不需要，可为 `None`。

    判定顺序:
    1. 先分类当前订单形状
    2. 优先检查是否命中 fill-driven rebuild
    3. 再检查 PAIR_MODE 是否满足 keep 条件
    4. 再检查 BUY_ONLY / SELL_ONLY 是否应 keep；其中 BUY_ONLY 额外评估 anchor break
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

    single_sided_action = detect_single_sided_action(
        current_btc_mid,
        orders,
        state,
        order_shape,
    )
    if single_sided_action is not None:
        return single_sided_action

    return "abnormal", None
