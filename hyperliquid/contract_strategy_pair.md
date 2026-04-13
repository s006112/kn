# Pair Strategy Contract

## Document Scope

本文档整合 pair strategy contract、anchor break 子合同与 pair strategy decision flow 的语义。

它依赖父合同 `contract_grid_engine.md` 已定义的 engine state model、live-state input boundary、open-order shape model、主循环判定顺序、rebuild success / failure 与 abnormal / exit contract。

本文档只描述当前 single-pair strategy 中成立的 contract 与内部 decision flow，不定义 engine 通用循环，也不定义未来 rolling ladder 或 multi-level strategy。

## Purpose

当前 pair strategy 的职责是：

- 在 engine 已接受的 `PAIR` / `BUY_ONLY` / `SELL_ONLY` saved state 之上
  定义 single-pair strategy 自己的 keep / rebuild / abnormal 接受规则
- 消费 engine 提供的当前 live state / live orders
- 把当前 live orders 解释为：
  - 合法 pair continuation
  - fill-driven residual transition
  - 单边 residual keep
  - 单边 residual stale rebuild
  - abnormal
- 为 engine 提供唯一策略判定结果：
  - `("keep", None)`
  - `("rebuild", reference_price)`
  - `("rebuild", None)`
  - `("abnormal", None)`

这些语义只依赖 engine 提供的当前 live state / live orders / 当前值，不依赖 transport / input mechanism 的具体形式。

## Document Structure

当前文档位于 engine contract 之下，并在本文档内部包含：

- Pair Strategy Contract
- Anchor Break Subcontract
- Pair Strategy Decision Flow

## Strategy Scope

当前 pair strategy 只适用于以下对象：

- saved state 只来自当前 single-pair implementation
- live orders 只围绕一个 buy price 与一个 sell price 的配对或残单展开
- 单边 residual 只允许是单笔合法买残单或单笔合法卖残单

当前 strategy 不处理：

- 多层 grid
- 滑动窗口 ladder
- 多于 `1 BUY + 1 SELL` 的合法策略状态
- 任意 abnormal live orders 的部分修复

## Strategy State Interpretation

在父合同允许的三个 `mode` 中，当前 pair strategy 的语义解释为：

### `PAIR`

表示当前策略承认：

- 保存态中应存在一组严格有效的 buy / sell pair
- 当前 live orders 若要继续 `keep`，必须仍然是同一组价格上的严格 pair
- 若其中一侧成交并只剩另一侧 residual，则进入 fill-driven rebuild 判定

### `BUY_ONLY`

表示当前策略承认：

- 上一轮下单或重建后，只剩单笔 buy residual 属于合法中间态
- 该中间态可以继续 `keep`
- 若该 residual 完成成交，则进入 fill completion rebuild
- 若该 residual 已不再符合本文档中的 Anchor Break Subcontract，则进入 stale rebuild

### `SELL_ONLY`

表示当前策略承认：

- 上一轮下单或重建后，只剩单笔 sell residual 属于合法中间态
- 该中间态可以继续 `keep`
- 若该 residual 完成成交，则进入 fill completion rebuild
- 当前实现中，`SELL_ONLY` 不执行 anchor break 检查

## Accepted Strategy Outcomes

当前 pair strategy 只接受以下返回结果：

- `("keep", None)`
- `("rebuild", reference_price)`
- `("rebuild", None)`
- `("abnormal", None)`

其中：

- fill-driven rebuild 的 `reference_price` 必须沿用“已成交那一侧”的保存价格
- Anchor Break 触发的 stale rebuild 必须返回 `("rebuild", None)`

## Strategy Decision Order

当前 pair strategy 必须严格按以下顺序判定：

1. classify 当前 open-order shape
2. 优先检查 fill-driven rebuild
3. 再检查 `PAIR` keep
4. 再检查单边 residual 分支
5. 以上都不满足则返回 `abnormal`

其中：

- 单边 residual 分支只在 saved `mode` 为 `BUY_ONLY` 或 `SELL_ONLY` 时评估
- 单边 residual 分支永远不能覆盖 fill-driven rebuild
- 单边 residual 分支永远晚于 `PAIR` keep

## Fill-Driven Rebuild Semantics

当前 pair strategy 接受以下 fill-driven rebuild 路径：

### Saved `PAIR`

当 saved `mode == PAIR` 时：

- 当前 live shape 为 `SELL_ONLY`，表示 buy side 已成交
- 当前 live shape 为 `BUY_ONLY`，表示 sell side 已成交

此时策略必须返回：

- `("rebuild", state["buy_price"])`，当 buy 已成交
- `("rebuild", state["sell_price"])`，当 sell 已成交

这里的 `reference_price` 必须沿用“已成交那一侧”的保存价格。

### Saved `BUY_ONLY`

当 saved `mode == BUY_ONLY` 且当前 `open orders == 0` 时：

- 视为合法 residual completion
- 必须返回 `("rebuild", state["buy_price"])`

### Saved `SELL_ONLY`

当 saved `mode == SELL_ONLY` 且当前 `open orders == 0` 时：

- 视为合法 residual completion
- 必须返回 `("rebuild", state["sell_price"])`

只要命中以上路径，就不得落入 keep，也不得落入 abnormal。

## `PAIR` Keep Contract

只有同时满足以下条件时，当前 pair strategy 才接受 `PAIR` keep：

- saved `mode == PAIR`
- 当前 live shape 仍为 `PAIR`
- `get_pair_state(...)` 成功
- 当前 `buy_price` 严格等于 saved `buy_price`
- 当前 `sell_price` 严格等于 saved `sell_price`

否则 `PAIR` 不得 `keep`。

## Single-Sided Branch Contract

### `BUY_ONLY` branch

只有同时满足以下条件时，当前 strategy 才接受 `BUY_ONLY` 分支：

- saved `mode == BUY_ONLY`
- 当前 live shape == `BUY_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单必须是买单

在此基础上：

- 若未触发本文档中的 Anchor Break Subcontract，则返回 `("keep", None)`
- 若触发本文档中的 Anchor Break Subcontract，则返回其定义的 rebuild 结果

### `SELL_ONLY` branch

只有同时满足以下条件时，当前 strategy 才接受 `SELL_ONLY` 分支：

- saved `mode == SELL_ONLY`
- 当前 live shape == `SELL_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单必须是卖单

在当前实现中：

- `SELL_ONLY` 只允许 `keep`
- `SELL_ONLY` 不执行 anchor break 检查

若上述条件不成立，则 `SELL_ONLY` 分支不得被接受。

## Abnormal Boundary

凡是不属于以下已接受路径的 live state，当前 pair strategy 一律不接受：

- fill-driven rebuild
- `PAIR` keep
- 合法 `BUY_ONLY` branch
- 合法 `SELL_ONLY` branch
- `BUY_ONLY` stale rebuild

例如：

- saved `PAIR`，但当前是无效 pair
- saved 单边模式，但 live shape 与 saved mode 不一致
- saved 单边模式，但 live orders 数量超过 `1`
- `BUY_ONLY` 分支中出现卖单 residual
- `SELL_ONLY` 分支中出现买单 residual
- 单边分支 guard 不成立，且也未命中 fill completion

这些情况都必须落入 `abnormal`，不得由当前 strategy 做部分修复。

## Logging Boundary

当前 pair strategy 只定义以下日志语义边界：

- `PAIR` keep 使用 pair keep 日志
- `BUY_ONLY` keep 使用 buy-only keep 日志
- `SELL_ONLY` keep 使用 sell-only keep 日志
- fill-driven rebuild 使用 fill completion / fill detected 日志
- anchor stale rebuild 的专属日志由 Anchor Break Subcontract 定义

# Anchor Break Subcontract

## Purpose

Anchor Break 是“合法单边 residual order”上的兜底分支。

它的作用是：当系统当前仍处于有效 `BUY_ONLY` 单边模式，且唯一 residual buy order 与当前 BTC mid 的偏离已经足够大时，不再继续 `keep`，而是改走 `rebuild`。

它只约束“当前 BTC mid 值满足什么条件时触发 stale rebuild”，不规定该当前值由 engine 通过何种 transport / infra 提供。

它不是通用恢复机制，也不处理任意 abnormal 订单形态。

## Scope

Anchor Break 只对以下对象有效：

- saved `mode` 为 `BUY_ONLY`
- 当前 `order_shape` 与 saved 单边模式完全一致
- 当前恰好只有 `1` 笔 residual order
- 该 residual order 必须是买单

对于 `SELL_ONLY`，Anchor Break 不生效；`SELL_ONLY` 只允许走自身 keep / fill completion / abnormal 的父合同路径。

除此之外，Anchor Break 不生效。

## Trigger Conditions

只有同时满足以下条件时，Anchor Break 才会触发：

- `REANCHOR_BREAK == True`
- saved `mode` 是 `BUY_ONLY`
- 当前分类结果与 saved 单边模式完全一致
- 当前 `open orders` 数量恰好为 `1`
- 该 residual order 是买单
- BTC mid 可以读取，且大于 `0`
- residual 与 BTC mid 的距离达到 `REANCHOR_BREAK_STEPS * GRID_STEP`

距离判定规则：

- buy residual: `btc_mid - order_price >= REANCHOR_BREAK_STEPS * GRID_STEP`

当前实现中不存在 `SELL_ONLY` Anchor Break trigger。
只要任一 guard 不满足，就不能触发 Anchor Break。

## Action Semantics

当 Anchor Break 触发时，单边分支返回 `("rebuild", None)`。

这表示后续 `rebuild(...)` 会：

- 先取消当前 residual order
- 因为显式传入的是 `reference_price=None`，所以改由 `get_mid_reference_price()` 推导新的锚点
- 用这个 fresh mid-grid anchor 重新下单

它不是沿用旧 residual 价格的 fill-driven rebuild。

## Priority

Anchor Break 的优先级关系必须满足：

- 它只在单边分支内部评估
- 它永远晚于 fill-driven rebuild
- 它永远晚于 order shape 校验
- 一旦触发，它覆盖当前实现中的 `BUY_ONLY` keep

## Non-Goals

Anchor Break 不负责：

- 覆盖或替代 fill-driven rebuild
- 修复 mode / shape mismatch
- 修复多单、多余残单或无效 pair
- 接受不合法的单边 residual
- 定义 engine 级 rebuild success / failure

# Pair Strategy Decision Flow

## Purpose

本部分只描述当前 single-pair strategy 的内部判定顺序，不描述 engine shell 的执行细节。
它也不描述 engine 通过何种 transport / infra 提供 current live orders 或 BTC mid。

本部分只是把前两部分的合同语义按固定流程重排，不新增行为。

## Decision Order

当前 pair strategy 的内部顺序必须严格固定为：

1. classify 当前 open-order shape
2. 优先检查 fill-driven rebuild
3. 再检查 `PAIR` keep
4. 再检查单边 residual branch
5. 以上都不满足则返回 `abnormal`

## Step 1: Shape Classification

strategy 首先读取当前 live orders，并将其分类为：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`
- `ABNORMAL`

这里的 shape classification 只是 strategy 后续判定的输入，不直接决定最终动作。

## Step 2: Fill-Driven Rebuild

fill-driven rebuild 拥有当前 strategy 的最高优先级。

### Saved `PAIR`

当 saved `mode == PAIR` 时：

- 若当前 live shape == `SELL_ONLY`，表示 buy 已成交
  返回 `("rebuild", state["buy_price"])`
- 若当前 live shape == `BUY_ONLY`，表示 sell 已成交
  返回 `("rebuild", state["sell_price"])`

### Saved `BUY_ONLY`

当 saved `mode == BUY_ONLY` 且当前 `open orders == 0` 时：

- 视为 buy residual completion
- 返回 `("rebuild", state["buy_price"])`

### Saved `SELL_ONLY`

当 saved `mode == SELL_ONLY` 且当前 `open orders == 0` 时：

- 视为 sell residual completion
- 返回 `("rebuild", state["sell_price"])`

只要命中 fill-driven rebuild，就不得继续评估 keep，也不得返回 abnormal。

## Step 3: `PAIR` Keep

只有同时满足以下条件时，strategy 才接受 `PAIR` keep：

- saved `mode == PAIR`
- 当前 live shape == `PAIR`
- `get_pair_state(...)` 成功
- 当前 `buy_price` 严格等于 saved `buy_price`
- 当前 `sell_price` 严格等于 saved `sell_price`

若成立：

- 返回 `("keep", None)`

若不成立：

- 不得 keep
- 继续进入单边 residual branch

## Step 4: Single-Sided Branch

单边 residual branch 只在 saved `mode == BUY_ONLY` 或 `SELL_ONLY` 时评估。

### `BUY_ONLY` path

只有同时满足以下条件时，才接受 `BUY_ONLY` branch：

- saved `mode == BUY_ONLY`
- 当前 live shape == `BUY_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单必须是买单

在此基础上：

- 若未触发 Anchor Break，则返回 `("keep", None)`
- 若触发 Anchor Break，则返回其定义的 stale rebuild 结果

### `SELL_ONLY` path

只有同时满足以下条件时，才接受 `SELL_ONLY` branch：

- saved `mode == SELL_ONLY`
- 当前 live shape == `SELL_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单必须是卖单

在当前实现中：

- `SELL_ONLY` 只允许 `keep`
- `SELL_ONLY` 不执行 anchor break 检查

若成立：

- 返回 `("keep", None)`

若不成立：

- 不得接受该分支

## Step 5: Abnormal Fallback

凡是不属于以下已接受路径的 live state，一律返回：

- `("abnormal", None)`

已接受路径仅包括：

- fill-driven rebuild
- `PAIR` keep
- 合法 `BUY_ONLY` branch
- 合法 `SELL_ONLY` branch
- `BUY_ONLY` stale rebuild

strategy 不负责部分修复，不负责兜底恢复，也不负责把 abnormal 强行转成 rebuild。

## Anchor Subcontract Boundary

Anchor Break 只在以下位置被评估：

- 单边 residual branch 内部
- 且仅限 `BUY_ONLY` branch

它永远不能：

- 早于 fill-driven rebuild
- 覆盖 `PAIR` keep
- 覆盖 `SELL_ONLY` keep
- 处理任意 abnormal 订单形态

## Out Of Scope

本文档不定义：

- engine shell startup / shutdown
- rebuild 的执行顺序
- 下单、撤单、清理与重试
- future rolling ladder / fixed-M moving window strategy

具体日志文本、限流实现与输出方式不由本文档定义。
