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
- parse: 订单 -> 当前 live structure
- compare: live structure -> saved expected pair state
- decide: fill-driven rebuild -> pair keep -> single-sided branch -> abnormal

不变式
- 本模块只做策略判定，不直接下单、撤单或执行 rebuild。
- 当前 decision order 固定为：
  parse -> compare -> decide
  且 decide 内部顺序固定为：
  fill-driven rebuild -> pair keep -> single-sided branch -> abnormal
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


def parse_pair_live_structure(
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
    解析当前 live orders，产出 pair strategy 使用的 live structure。

    输入:
    orders: 当前挂单集合。
    其余参数为当前 pair runtime 使用的配置常量。

    输出:
    返回一个只描述“当前 live structure”的映射，至少包含：
    - `orders`
    - `order_count`
    - `order_shape`

    这里的 parse 结果不直接决定最终动作，它只是 compare layer 的输入。
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

    return {
        "orders": orders,
        "order_count": len(orders),
        "order_shape": order_shape,
    }


def compare_pair_live_vs_expected(
    state,
    live_structure,
    *,
    grid_step,
    buy_grid_factor,
    sell_grid_factor,
    pair_mode,
    buy_only_mode,
    sell_only_mode,
):
    """作用:
    将当前 live structure 与 saved expected pair state 做当前 pair 语义下的比较。

    输入:
    state: 至少包含 `mode`、`buy_price` 和 `sell_price` 的已保存状态映射。
    live_structure: `parse_pair_live_structure()` 返回的 live structure。
    其余参数为当前 pair runtime 使用的配置常量。

    输出:
    返回一个 pair-local compare result，显式表达：
    - 是否命中 fill-driven rebuild
    - 是否命中 `PAIR` keep
    - 是否命中合法单边分支
    - 是否应落入 abnormal

    这是当前 pair strategy 的 compare layer，不代表 ladder `M = 1`
    已经完成吸收。
    """
    orders = live_structure["orders"]
    order_shape = live_structure["order_shape"]
    current_pair = None

    if order_shape == pair_mode:
        current_pair = get_pair_state(
            orders,
            grid_step,
            buy_grid_factor,
            sell_grid_factor,
            pair_mode,
        )

    fill_rebuild_reason = None
    fill_rebuild_reference_price = None
    previous_mode = state["mode"]

    if previous_mode == pair_mode:
        if order_shape == sell_only_mode:
            fill_rebuild_reason = "buy_filled"
            fill_rebuild_reference_price = state["buy_price"]
        elif order_shape == buy_only_mode:
            fill_rebuild_reason = "sell_filled"
            fill_rebuild_reference_price = state["sell_price"]
    elif previous_mode == buy_only_mode and live_structure["order_count"] == 0:
        fill_rebuild_reason = "buy_only_completed"
        fill_rebuild_reference_price = state["buy_price"]
    elif previous_mode == sell_only_mode and live_structure["order_count"] == 0:
        fill_rebuild_reason = "sell_only_completed"
        fill_rebuild_reference_price = state["sell_price"]

    is_pair_keep_candidate = (
        previous_mode == pair_mode
        and order_shape == pair_mode
        and current_pair is not None
        and pair_matches_state(current_pair, state)
    )

    single_sided_branch_mode = None
    if (
        previous_mode == buy_only_mode
        and order_shape == buy_only_mode
        and live_structure["order_count"] == 1
        and orders[0]["side"] == "B"
    ):
        single_sided_branch_mode = buy_only_mode
    elif (
        previous_mode == sell_only_mode
        and order_shape == sell_only_mode
        and live_structure["order_count"] == 1
        and orders[0]["side"] != "B"
    ):
        single_sided_branch_mode = sell_only_mode

    is_fill_rebuild_candidate = fill_rebuild_reason is not None
    is_single_sided_branch_candidate = single_sided_branch_mode is not None
    is_abnormal_candidate = not (
        is_fill_rebuild_candidate
        or is_pair_keep_candidate
        or is_single_sided_branch_candidate
    )

    return {
        "expected_state": {
            "mode": state["mode"],
            "buy_price": state["buy_price"],
            "sell_price": state["sell_price"],
        },
        "live_structure": live_structure,
        "current_pair": current_pair,
        "relation": {
            "is_fill_rebuild_candidate": is_fill_rebuild_candidate,
            "is_pair_keep_candidate": is_pair_keep_candidate,
            "is_single_sided_branch_candidate": is_single_sided_branch_candidate,
            "single_sided_branch_mode": single_sided_branch_mode,
            "is_abnormal_candidate": is_abnormal_candidate,
        },
        "fill_rebuild": {
            "reason": fill_rebuild_reason,
            "reference_price": fill_rebuild_reference_price,
        },
    }


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


def decide_pair_action_from_compare(
    info,
    state,
    compare_result,
    *,
    buy_only_mode,
    sell_only_mode,
    reanchor_break,
    btc_mid_key,
    grid_step,
    reanchor_break_steps,
    log_keep_state,
    log_msg,
    format_price,
):
    """作用:
    根据 pair-local compare result 决定当前唯一动作。

    输入:
    info: 用于评估 anchor break 的 Hyperliquid `Info` 客户端。
    state: 至少包含 `mode` 的已保存状态映射。
    compare_result: `compare_pair_live_vs_expected()` 返回的 compare result。
    其余参数为 decide 所需配置与日志函数。

    输出:
    返回当前 pair strategy 对外唯一允许的动作：
    - `("keep", None)`
    - `("rebuild", reference_price)`
    - `("rebuild", None)`
    - `("abnormal", None)`
    """
    fill_rebuild = compare_result["fill_rebuild"]
    if compare_result["relation"]["is_fill_rebuild_candidate"]:
        fill_reason = fill_rebuild["reason"]

        if fill_reason == "buy_filled":
            log_msg(f"🔥 BUY filled - {format_price(state['buy_price'])}")
        elif fill_reason == "sell_filled":
            log_msg(f"✅ SELL filled - {format_price(state['sell_price'])}")
        elif fill_reason == "buy_only_completed":
            log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
        elif fill_reason == "sell_only_completed":
            log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")

        return "rebuild", fill_rebuild["reference_price"]

    if compare_result["relation"]["is_pair_keep_candidate"]:
        log_keep_state("pair", "contract: keep pair")
        return "keep", None

    live_structure = compare_result["live_structure"]
    single_sided_branch_mode = compare_result["relation"]["single_sided_branch_mode"]
    if single_sided_branch_mode == buy_only_mode:
        anchor_break_action = detect_anchor_break(
            info,
            live_structure["orders"],
            state,
            live_structure["order_shape"],
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

    if single_sided_branch_mode == sell_only_mode:
        log_keep_state("sell-only", "contract: keep sell-only")
        return "keep", None

    return "abnormal", None


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
    1. parse 当前 live structure
    2. compare live structure 与 saved expected pair state
    3. decide 唯一动作

    其中 decide 内部顺序固定为：
    - 优先检查 fill-driven rebuild
    - 再检查 `PAIR` keep
    - 再检查 `BUY_ONLY` / `SELL_ONLY` 分支
    - 最后落入 abnormal

    返回:
    - ("keep", None)
    - ("rebuild", reference_price 或 None)
    - ("abnormal", None)

    边界:
    本函数只负责决策，不负责下单、撤单或实际 rebuild 执行。
    """
    live_structure = parse_pair_live_structure(
        orders,
        grid_step,
        buy_grid_factor,
        sell_grid_factor,
        pair_mode,
        buy_only_mode,
        sell_only_mode,
        abnormal_mode,
    )

    compare_result = compare_pair_live_vs_expected(
        state,
        live_structure,
        grid_step=grid_step,
        buy_grid_factor=buy_grid_factor,
        sell_grid_factor=sell_grid_factor,
        pair_mode=pair_mode,
        buy_only_mode=buy_only_mode,
        sell_only_mode=sell_only_mode,
    )

    return decide_pair_action_from_compare(
        info,
        state,
        compare_result,
        buy_only_mode=buy_only_mode,
        sell_only_mode=sell_only_mode,
        reanchor_break=reanchor_break,
        btc_mid_key=btc_mid_key,
        grid_step=grid_step,
        reanchor_break_steps=reanchor_break_steps,
        log_keep_state=log_keep_state,
        log_msg=log_msg,
        format_price=format_price,
    )
