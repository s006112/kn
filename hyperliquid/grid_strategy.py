"""grid_strategy.py

职责
该模块承载当前 single-pair strategy 的纯决策内核：
读取 engine 传入的 saved state 与当前 live orders，
并返回本轮唯一策略动作：

- ("keep", None)
- ("rebuild", reference_price)
- ("rebuild", None)
- ("abnormal", None)

流程:
- 订单 -> 形态分类 -> fill-driven rebuild
- 状态 -> pair keep 校验
- 单边 residual -> keep / anchor break
- 未命中 -> abnormal

不变式
- 本模块只做策略判定，不直接下单、撤单或执行 rebuild。
- 当前 decision order 固定为：
  classify -> fill-driven rebuild -> pair keep -> single-sided branch -> abnormal
- 当前实现中，anchor break 仅适用于 BUY_ONLY residual。

范围外
- 不负责 cleanup、下单执行、交易所 client、reference price 推导和主循环调度。
- 不负责预算 sizing、状态持久化和 multi-grid / ladder 行为。
"""

from grid_logic import (
    classify_order_shape,
    get_pair_state,
    pair_matches_state,
    should_reanchor_residual_order,
)


def resolve_fill_rebuild_action(
    state,
    orders,
    order_shape,
    pair_mode,
    buy_only_mode,
    sell_only_mode,
    log_msg,
    format_price,
):
    """作用:
    判断是否发生了成交驱动的重建条件，并返回对应动作。

    输入:
    state: 至少包含 `mode`、`buy_price` 和 `sell_price` 的状态映射。该状态可能来自
    `get_pair_state()`（包含 `pair_center_price`）或 `place_pair()`（包含 `reference_price`），
    但本函数不要求这两个字段存在，也不将它们视为同一概念。
    orders: 当前挂单集合，用于检查残单是否已经完成。
    order_shape: `orders` 的当前形态分类结果。
    pair_mode / buy_only_mode / sell_only_mode: 策略模式常量。
    log_msg: 日志函数。
    format_price: 价格格式化函数。

    输出:
    当先前状态为 `PAIR` 且当前变为单边残单时，返回 `("rebuild", reference_price)`，
    并使用已成交那一侧此前的价格作为重建参考价。
    对于 `BUY_ONLY` 或 `SELL_ONLY` 状态，当当前已无挂单时，也返回
    `("rebuild", reference_price)`，并使用该残单侧保存的价格。
    其余情况返回 `None`。
    """
    previous_mode = state["mode"]

    if previous_mode == pair_mode:
        if order_shape == sell_only_mode:
            log_msg(f"🔥 BUY filled - {format_price(state['buy_price'])}")
            return "rebuild", state["buy_price"]

        if order_shape == buy_only_mode:
            log_msg(f"✅ SELL filled - {format_price(state['sell_price'])}")
            return "rebuild", state["sell_price"]

        return None

    if previous_mode == buy_only_mode and len(orders) == 0:
        log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
        return "rebuild", state["buy_price"]

    if previous_mode == sell_only_mode and len(orders) == 0:
        log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
        return "rebuild", state["sell_price"]

    return None


def validate_keep_state(
    orders,
    state,
    order_shape,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_mode,
    log_keep_state,
):
    """作用:
    判断当前订单是否仍满足成对状态的 keep 条件。

    输入:
    orders: 当前挂单集合。
    state: 至少包含 `mode`、`buy_price` 和 `sell_price` 的已保存状态映射。该状态可能来自
    `get_pair_state()` 并带有 `pair_center_price`，也可能来自 `place_pair()` 并带有
    `reference_price`；本函数只校验保存的买卖价格，不假定这两个字段等价。
    order_shape: `orders` 的当前形态分类结果。
    grid_step / buy_grid_factor / sell_grid_factor / pair_mode: 策略配置常量。
    log_keep_state: keep 日志函数。

    输出:
    仅在 `PAIR` 状态下可能返回 `True`。仅当 `order_shape` 为 `PAIR`、
    `get_pair_state()` 成功且 `pair_matches_state()` 确认两侧价格与已保存状态完全一致时
    返回 `True`。其余路径均返回 `False`。
    """
    if state["mode"] == pair_mode:
        if order_shape != pair_mode:
            return False

        current_pair = get_pair_state(
            orders,
            grid_step,
            buy_grid_factor,
            sell_grid_factor,
            pair_mode,
        )
        if current_pair is None:
            return False

        if not pair_matches_state(current_pair, state):
            return False

        log_keep_state("pair", "contract: keep pair")
        return True

    return False


def detect_anchor_break(
    info,
    orders,
    state,
    order_shape,
    *,
    reanchor_break,
    buy_only_mode,
    sell_only_mode,
    btc_mid_key,
    grid_step,
    reanchor_break_steps,
    log_msg,
):
    """作用:
    检测合法 BUY_ONLY 残单是否已经偏离 BTC 中间价到必须重建的程度。

    输入:
    info: 被残单重锚辅助函数使用的 Hyperliquid `Info` 客户端。
    orders: 当前挂单集合。
    state: 至少包含 `mode` 的已保存状态映射。
    order_shape: `orders` 的当前形态分类结果。
    其余参数为 anchor break 所需配置与日志函数。

    输出:
    仅当以下条件全部成立时，返回 `("rebuild", None)`：
    - `reanchor_break` 已开启
    - `state["mode"] == BUY_ONLY`
    - `order_shape == BUY_ONLY`
    - 当前恰好只有一笔订单
    - 该订单必须是买单
    - `should_reanchor_residual_order()` 返回 `True`

    其余路径返回 `None`。
    """
    if not reanchor_break:
        return None

    if state["mode"] != buy_only_mode:
        return None

    if order_shape != buy_only_mode:
        return None

    if len(orders) != 1:
        return None

    if orders[0]["side"] != "B":
        return None

    if not should_reanchor_residual_order(
        info,
        orders,
        state["mode"],
        buy_only_mode,
        sell_only_mode,
        btc_mid_key,
        grid_step,
        reanchor_break,
        reanchor_break_steps,
    ):
        return None

    log_msg("🪝 contract: anchor break")
    return "rebuild", None


def detect_single_sided_action(
    info,
    orders,
    state,
    order_shape,
    *,
    buy_only_mode,
    sell_only_mode,
    reanchor_break,
    btc_mid_key,
    grid_step,
    reanchor_break_steps,
    log_keep_state,
    log_msg,
):
    """作用:
    决定单边状态应继续保持还是触发重建。

    输入:
    info: 用于评估 anchor break 的 Hyperliquid `Info` 客户端。
    orders: 当前挂单集合。
    state: 至少包含 `mode` 的已保存状态映射。
    order_shape: `orders` 的当前形态分类结果。
    其余参数为单边判定所需配置与日志函数。

    输出:
    - 对于 `BUY_ONLY` 模式：
      - 若未触发 anchor break，返回 `("keep", None)`
      - 若触发 anchor break，返回 `("rebuild", None)`
    - 对于 `SELL_ONLY` 模式：
      - 始终返回 `("keep", None)`（不执行 anchor break）

    当保存模式不是单边、当前形态与保存模式不一致，或残单方向与预期方向不一致时，
    返回 `None`。
    """
    if state["mode"] == buy_only_mode:
        if order_shape != buy_only_mode:
            return None
        if len(orders) != 1 or orders[0]["side"] != "B":
            return None

        anchor_break_action = detect_anchor_break(
            info,
            orders,
            state,
            order_shape,
            reanchor_break=reanchor_break,
            buy_only_mode=buy_only_mode,
            sell_only_mode=sell_only_mode,
            btc_mid_key=btc_mid_key,
            grid_step=grid_step,
            reanchor_break_steps=reanchor_break_steps,
            log_msg=log_msg,
        )
        if anchor_break_action is not None:
            return anchor_break_action

        log_keep_state("buy-only", "contract: keep buy-only")
        return "keep", None

    if state["mode"] == sell_only_mode:
        if order_shape != sell_only_mode:
            return None
        if len(orders) != 1 or orders[0]["side"] == "B":
            return None

        log_keep_state("sell-only", "contract: keep sell-only")
        return "keep", None

    return None


def get_loop_action(
    info,
    orders,
    state,
    *,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_mode,
    buy_only_mode,
    sell_only_mode,
    abnormal_mode,
    reanchor_break,
    btc_mid_key,
    reanchor_break_steps,
    log_msg,
    log_keep_state,
    format_price,
):
    """作用:
    根据当前 open orders 与上一轮已接受的 state，
    判定这一轮主循环应执行的唯一动作。

    输入:
    info: 用于订单状态判断和潜在重建决策的 Hyperliquid `Info` 客户端。
    orders: 当前挂单集合。
    state: 上一轮已接受的状态映射。
    其余参数为策略判定所需配置常量与日志/格式化函数。

    判定顺序:
    1. 先分类当前订单形状
    2. 优先检查是否命中 fill-driven rebuild
    3. 再检查 PAIR 是否满足 keep 条件
    4. 再检查 BUY_ONLY / SELL_ONLY 是否应 keep 或触发 anchor break
    5. 若以上都不满足，则判定为 abnormal

    返回:
    - ("keep", None)
    - ("rebuild", reference_price 或 None)
    - ("abnormal", None)

    边界:
    本函数只负责决策，不负责下单、撤单或实际 rebuild 执行。
    """
    order_shape = classify_order_shape(
        orders,
        grid_step,
        buy_grid_factor,
        sell_grid_factor,
        pair_mode,
        buy_only_mode,
        sell_only_mode,
        abnormal_mode,
    )

    fill_driven_action = resolve_fill_rebuild_action(
        state,
        orders,
        order_shape,
        pair_mode,
        buy_only_mode,
        sell_only_mode,
        log_msg,
        format_price,
    )
    if fill_driven_action is not None:
        return fill_driven_action

    if validate_keep_state(
        orders,
        state,
        order_shape,
        grid_step,
        buy_grid_factor,
        sell_grid_factor,
        pair_mode,
        log_keep_state,
    ):
        return "keep", None

    single_sided_action = detect_single_sided_action(
        info,
        orders,
        state,
        order_shape,
        buy_only_mode=buy_only_mode,
        sell_only_mode=sell_only_mode,
        reanchor_break=reanchor_break,
        btc_mid_key=btc_mid_key,
        grid_step=grid_step,
        reanchor_break_steps=reanchor_break_steps,
        log_keep_state=log_keep_state,
        log_msg=log_msg,
    )
    if single_sided_action is not None:
        return single_sided_action

    return "abnormal", None