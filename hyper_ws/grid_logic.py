"""grid_logic.py

职责
该辅助模块负责为 Hyperliquid 网格循环分类当前挂单形态，从已有订单推导经过校验的成对订单状态，将当前成对价格与已保存状态进行比较，并基于调用方提供的 BTC 中间价判断单边残单是否已偏离到需要重锚的程度。

Used by:
* hyperliquid/grid_decision.py

流程:
- 订单 -> 计数 -> 配对状态 -> 形态
- 状态 -> 价格 -> 严格匹配
- 模式 -> 传入中间价 -> 距离 -> 重锚

不变式
- `side == "B"` 始终按买单计数，其余 side 值一律按卖单计数。
- 只有当恰好存在一个买价和一个卖价，且两个价格都为正、买价低于卖价、并且价差严格等于 `(buy_grid_factor + sell_grid_factor) * grid_step` 时，才会返回成对状态。
- 当模式不是单边、重锚功能关闭、订单数不为一、或传入 BTC 中间价非正时，残单重锚检查始终返回 `False`。

范围外
- 不负责下单、撤单或修改订单。
- 不负责日志、重试和循环控制。
- 不负责预算 sizing、参考价构造和交易所凭证处理。
"""

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
    mode,
    buy_only_mode,
    sell_only_mode,
    btc_mid,
    grid_step,
    reanchor_break,
    reanchor_break_steps,
):
    """作用:
    判断在 BTC 中间价远离单边残单后，该订单是否需要重锚。

    输入:
    orders: 序列类型的订单映射对象，预期包含 `side` 和 `limitPx` 字段。
    mode: 当前策略模式值。
    buy_only_mode: 表示买侧残单的模式值。
    sell_only_mode: 表示卖侧残单的模式值。
    btc_mid: 调用方已读取并解析的 BTC 中间价。
    grid_step: 与 `reanchor_break_steps` 相乘后形成阈值距离的价格步长。
    reanchor_break: 是否启用残单重锚检查的开关。
    reanchor_break_steps: 触发重锚所需的网格步数。

    输出:
    仅当重锚功能开启、`mode` 为单边模式、恰好存在一笔订单、传入的 BTC 中间价为正，并且中间价与残单价格的距离至少达到 `reanchor_break_steps * grid_step` 时返回 `True`。对于不允许的模式、关闭的检查、非法订单数量或非正中间价，返回 `False`。在通过中间价保护性检查后，透传异常订单字段导致的取值和浮点转换异常。
    """
    if mode not in (buy_only_mode, sell_only_mode):
        return False

    if not reanchor_break:
        return False

    if len(orders) != 1:
        return False

    if btc_mid is None or btc_mid <= 0:
        return False

    threshold_distance = reanchor_break_steps * grid_step
    order = orders[0]
    order_price = float(order["limitPx"])

    if order["side"] == "B":
        return btc_mid - order_price >= threshold_distance

    return order_price - btc_mid >= threshold_distance
