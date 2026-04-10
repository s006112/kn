# Anchor Break + Single-Sided Contract v3.0

本文档与当前 `hyperliquid/grid.py`、`hyperliquid/grid_logic.py` 的简化实现保持一致。

## 1. 目的

Anchor Break 只是“合法单边 residual order”上的一种兜底分支。

它的作用是：当系统当前仍处于有效的单边模式，但唯一 residual order 已经和当前 BTC mid 偏离过大时，不再继续 `keep`，而是改走 `rebuild`。

它不是通用恢复机制，也不会尝试修复任意 abnormal 订单形态。

## 2. 允许的模式

主循环中的 saved state 在运行过程中只允许为：
- PAIR
- BUY_ONLY
- SELL_ONLY

ABNORMAL 仅作为中间判定结果或错误返回值存在，不会作为有效状态进入主循环的下一轮。

当前 `open orders` 的形态分类规则如下：

- `PAIR`：恰好 `1 BUY + 1 SELL`，且两边价差满足当前配置的 grid gap 与 tolerance
- `BUY_ONLY`：恰好 `1 BUY + 0 SELL`
- `SELL_ONLY`：恰好 `0 BUY + 1 SELL`
- `ABNORMAL`：其他所有情况，包括无效的 `1 BUY + 1 SELL`、fill 逻辑之外的 `0 orders`、或任意多余残单

## 3. 决策顺序

每轮循环严格按以下顺序判定：

1. 先对当前 orders 做 `classify_order_shape(...)`
2. 优先检查 fill-driven rebuild
3. 再检查 `PAIR` 是否满足 keep
4. 再检查单边模式是否 keep，或是否触发 anchor break
5. 以上都不满足则返回 `abnormal`

因此：

- anchor break 永远不会覆盖 fill-driven rebuild
- anchor break 永远不会早于 mode/shape 校验执行

## 4. Fill-Driven Rebuild

fill-driven rebuild 的优先级高于 keep 和 anchor break。

### 4.1 从 saved `PAIR` 进入 fill-driven rebuild

当上一轮 saved `mode == PAIR` 时：

- 当前形态为 `SELL_ONLY`，表示之前的 buy 已成交
- 当前形态为 `BUY_ONLY`，表示之前的 sell 已成交

此时的 rebuild 行为是：

- 使用“已成交那一侧”的保存价格作为 `reference_price`
- 先取消当前剩余订单
- 再用这个显式的 `reference_price` 执行 rebuild

这里不是 mid-grid rebuild。

### 4.2 从 saved `BUY_ONLY` 进入 fill-driven rebuild

当上一轮 saved `mode == BUY_ONLY`，且当前 `open orders` 数量为 `0` 时：

- 视为合法 residual completion
- 使用保存的 `buy_price` 作为 `reference_price` 执行 rebuild

### 4.3 从 saved `SELL_ONLY` 进入 fill-driven rebuild

当上一轮 saved `mode == SELL_ONLY`，且当前 `open orders` 数量为 `0` 时：

- 视为合法 residual completion
- 使用保存的 `sell_price` 作为 `reference_price` 执行 rebuild

只要命中以上路径，residual completion 就不能被视为 abnormal。

## 5. Keep 规则

### 5.1 `PAIR` keep

只有同时满足以下条件时，`PAIR` 才会继续 `keep`：

- 当前分类结果仍然是 `PAIR`
- 对当前 orders 调用 `get_pair_state(...)` 成功
- 当前 buy price 与 saved `buy_price` 的偏差不超过 `PAIR_PRICE_TOLERANCE`
- 当前 sell price 与 saved `sell_price` 的偏差不超过 `PAIR_PRICE_TOLERANCE`

否则 `PAIR` 不会 keep。

### 5.2 `BUY_ONLY` keep

只有同时满足以下条件时，`BUY_ONLY` 才会继续 `keep`：

- saved `mode` 是 `BUY_ONLY`
- 当前分类结果也是 `BUY_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单的 `side` 必须是买单
- 没有触发 anchor break

### 5.3 `SELL_ONLY` keep

只有同时满足以下条件时，`SELL_ONLY` 才会继续 `keep`：

- saved `mode` 是 `SELL_ONLY`
- 当前分类结果也是 `SELL_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单的 `side` 必须是卖单
- 没有触发 anchor break

## 6. Anchor Break 触发条件

只有同时满足以下条件时，anchor break 才会触发：

- `REANCHOR_BREAK == True`
- saved `mode` 是 `BUY_ONLY` 或 `SELL_ONLY`
- 当前分类结果与 saved 单边模式完全一致
- 当前 `open orders` 数量恰好为 `1`
- BTC mid 可以读取，且大于 `0`
- residual 与 BTC mid 的距离达到 `REANCHOR_BREAK_STEPS * GRID_STEP`

距离判定规则：

- buy residual：`btc_mid - order_price >= REANCHOR_BREAK_STEPS * GRID_STEP`
- sell residual：`order_price - btc_mid >= REANCHOR_BREAK_STEPS * GRID_STEP`

只要任一 guard 不满足，就不能触发 anchor break。

## 7. Anchor Break 动作

当 anchor break 触发时，循环返回 `("rebuild", None)`。

这表示后续 `rebuild(...)` 会：

- 取消当前订单
- 通过 `get_mid_reference_price()` 从当前 BTC mid 推导新的 `reference_price`
- 用这个新的 mid-grid anchor 重新下单

也就是说，anchor break 之所以会走 fresh mid-grid rebuild，是因为它显式传入的是 `reference_price=None`。

## 8. Rebuild 结果约定

只有当 `place_pair(...)` 返回“非 abnormal mode”时，`rebuild(...)` 才算成功。

成功结果只有三种：

- `PAIR`：买卖两边都下单成功
- `BUY_ONLY`：buy 下单成功，sell 因 `Insufficient spot balance` 失败，且 `ALLOW_BUY_ONLY_WHEN_NO_BTC == True`
- `SELL_ONLY`：sell 下单成功，buy 因 `Insufficient spot balance` 失败，且 `ALLOW_SELL_ONLY_WHEN_NO_USDC == True`

其他所有情况都视为 rebuild failure，包括：

- `place_pair(...)` 返回 `ABNORMAL`
- rebuild 前清理订单失败
- 下单失败后的二次清理失败

一旦 rebuild failure，主循环立即退出。

## 9. 当前简化算法中的 `ABNORMAL`

当前算法是故意收窄的。凡是不属于前面已接受分支的情况，都会落入 `abnormal`。

例如：

- saved `PAIR`，但当前形态是无效 pair 或多余残单
- saved `BUY_ONLY`，但当前形态变成 `SELL_ONLY`
- saved `SELL_ONLY`，但当前形态变成 `BUY_ONLY`
- saved 单边模式，但当前 live orders 数量超过 `1`
- 当前 pair 价格已不再与 saved pair 价格匹配 tolerance
- fill / keep 检查之后，仍然无法归类到已接受分支的其他形态

当前实现不会尝试对这些情况做部分修复，而是记录 abnormal 并退出。

## 10. 日志约定

当前分支日志大致包括：

- `contract: keep pair`
- `contract: keep buy-only`
- `contract: keep sell-only`
- `contract: anchor break`
- `PAIR -> 单边` 的 fill 日志
- `单边 -> 0 orders` 的 residual completion 日志
- abnormal summary 与 abnormal exit 日志

其中：

- keep 类日志会做 rate limit
- anchor break、cleanup failure、rebuild failure、abnormal exit 不属于 keep rate limit 路径

## 11. 一句话

当前代码里的 Anchor Break，本质上是“合法单边 residual order 的 stale override keep 机制”；而所有合法 fill completion 都必须优先进入 fill-driven rebuild，其他未被显式接受的形态则统一落入 `abnormal`。
