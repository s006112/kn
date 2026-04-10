# Grid Engine Contract

本文档只描述当前 `hyperliquid/grid.py`、`hyperliquid/grid_logic.py` 中对整个 grid engine 都成立的规则。

## 1. Purpose

当前 engine 的职责是：

- 从当前 `open orders` 推导或承接一个已接受的运行态
- 在 `keep`、fill-driven `rebuild`、`abnormal exit` 三类结果之间做唯一决策
- 只在成功得到非 `ABNORMAL` 新状态后继续主循环

策略特定的单边 residual stale 处理不属于本文档，见子合同 `contract_anchor_break.md`。
- residual stale = “一个单边残单，而且它已经不再符合当前 grid / anchor / contract，因此必须触发 rebuild”

## 2. Accepted State Model

主循环中的 saved state 只接受以下 `mode`：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`

`ABNORMAL` 只作为判定结果或失败结果存在，不作为下一轮循环的有效 saved state。

当前实现承认两种状态 schema，且不会把它们视为同一字段语义：

- pair snapshot state: `mode`、`buy_price`、`sell_price`、`pair_center_price`
- placement / rebuild state: `mode`、`buy_price`、`sell_price`、`reference_price`

其中：

- `pair_center_price` 只是从当前有效 pair 快照推导出的中点
- `reference_price` 是下单或 rebuild 使用的锚点
- engine 不会在决策链中把两者合并或互相替代

## 3. Open-Order Shape Model

当前 `open orders` 形态只按以下规则分类：

- `PAIR`: 恰好 `1 BUY + 1 SELL`，且该 pair 严格满足理论价差 `sell_price - buy_price == (buy_grid_factor + sell_grid_factor) * grid_step`
- `BUY_ONLY`: 恰好 `1 BUY + 0 SELL`
- `SELL_ONLY`: 恰好 `0 BUY + 1 SELL`
- `ABNORMAL`: 其他所有情况

当前版本故意采用严格不变式边界：

- 只接受严格满足理论价差的 pair
- 不接受近似匹配或接近理论价差的 pair
- 不接受任何“差一点”的 live pair

`ABNORMAL` 包括但不限于：

- 无效的 `1 BUY + 1 SELL`
- fill contract 之外的 `0 orders`
- 任意多余残单

## 4. Main Loop Decision Order

每轮循环严格按以下顺序判定：

1. 对当前 orders 做 `classify_order_shape(...)`
2. 优先检查 fill-driven rebuild
3. 再检查 `PAIR` 是否满足 keep
4. 再检查单边状态的专属处理分支
5. 以上都不满足则返回 `abnormal`

其中第 4 步的单边专属处理由子合同定义；但它永远不能早于 shape 校验，也永远不能覆盖 fill-driven rebuild。

## 5. Fill-Driven Rebuild

fill-driven rebuild 的优先级高于任何 keep 分支和任何单边专属分支。

### 5.1 Saved `PAIR`

当 saved `mode == PAIR` 时：

- 当前形态为 `SELL_ONLY`，表示之前的 buy 已成交
- 当前形态为 `BUY_ONLY`，表示之前的 sell 已成交

此时必须进入 `("rebuild", reference_price)`，并使用“已成交那一侧”的保存价格作为 `reference_price`。

这里不是 fresh mid-grid rebuild。

### 5.2 Saved `BUY_ONLY`

当 saved `mode == BUY_ONLY` 且当前 `open orders` 数量为 `0` 时：

- 视为合法 residual completion
- 返回 `("rebuild", state["buy_price"])`

### 5.3 Saved `SELL_ONLY`

当 saved `mode == SELL_ONLY` 且当前 `open orders` 数量为 `0` 时：

- 视为合法 residual completion
- 返回 `("rebuild", state["sell_price"])`

只要命中以上路径，就不能把该 completion 当作 `abnormal`。

## 6. Keep Contract

### 6.1 `PAIR` keep

只有同时满足以下条件时，`PAIR` 才能继续 `keep`：

- 当前分类结果仍然是 `PAIR`
- `get_pair_state(...)` 成功
- 当前 `buy_price` 严格等于 saved `buy_price`
- 当前 `sell_price` 严格等于 saved `sell_price`

否则 `PAIR` 不得 `keep`。

### 6.2 Single-Sided Keep

`BUY_ONLY` / `SELL_ONLY` 是否允许继续 `keep`，由子合同定义；engine 只保证：

- 该判定发生在 fill-driven rebuild 之后
- 该判定发生在 `PAIR` keep 之后
- 若单边专属分支未接受当前状态，则结果落入 `abnormal`

## 7. Rebuild Success And Failure

`rebuild(...)` 只有在 `place_pair(...)` 返回“非 `ABNORMAL` mode”时才算成功。

成功结果只有三种：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`

其他所有情况都视为 rebuild failure，包括：

- `place_pair(...)` 返回 `ABNORMAL`
- rebuild 前清理订单失败
- 下单失败后的二次清理失败

一旦 rebuild failure，主循环立即退出。

## 8. Abnormal / Exit Contract

当前实现是故意收窄的。凡是不属于已接受分支的情况，都会落入 `abnormal` 并退出。

例如：

- saved `PAIR`，但当前形态是无效 pair 或多余残单
- saved `BUY_ONLY`，但当前形态变成 `SELL_ONLY`
- saved `SELL_ONLY`，但当前形态变成 `BUY_ONLY`
- saved 单边模式，但当前 live orders 数量超过 `1`
- 当前 pair 不再严格满足理论价差
- 当前 pair 的买卖价格与 saved pair 不再完全一致
- fill / keep / 单边专属分支检查后，仍未命中已接受分支的其他形态

当前实现不会尝试对这些情况做部分修复。

## 9. Logging Contract

当前 engine 日志约定包括：

- keep 类日志
- fill completion 日志
- abnormal summary 与 abnormal exit 日志
- rebuild failure / abnormal rebuild exit 日志

其中：

- keep 类日志走 rate limit
- 非 keep 退出类日志不走 keep rate limit

具体的单边专属分支日志由子合同定义。

## 10. Out Of Scope

本文档不定义：

- anchor break 的触发条件、动作语义和非目标
- 任意 abnormal 订单形态的通用恢复机制
- 当前实现之外的未来扩展策略
