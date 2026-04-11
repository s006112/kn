# flow_strategy_pair.md

本文档只描述当前 single-pair strategy 的内部判定顺序，不描述 engine shell 的执行细节。

它依赖：
- `contract_strategy_pair.md`
- `contract_strategy_anchor.md`

## 1. Purpose

当前 pair strategy 的职责是：

- 读取 engine 传入的 saved state 与当前 live orders
- 解释当前 live orders 属于哪一种 single-pair strategy 情况
- 返回唯一动作结果：
  - `("keep", None)`
  - `("rebuild", reference_price)`
  - `("rebuild", None)`
  - `("abnormal", None)`

本文档不定义 cleanup、place order、exchange client 或循环调度。

## 2. Pair Strategy Decision Flow

当前 pair strategy 的内部顺序必须严格固定为：

1. parse 当前 live structure
2. compare live structure 与 saved expected pair state
3. decide 唯一动作

其中 decide 内部顺序必须继续严格固定为：

1. 优先检查 fill-driven rebuild
2. 再检查 `PAIR` keep
3. 再检查单边 residual branch
4. 以上都不满足则返回 `abnormal`

## 3. Step 1: Parse Pair Live Structure

strategy 首先读取当前 live orders，并产出当前 pair strategy 使用的 live structure。

该 live structure 至少显式包含：

- 当前 `orders`
- 当前 `order_count`
- 当前 `order_shape`

其中 `order_shape` 仍按当前 runtime truth 分类为：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`
- `ABNORMAL`

若当前 `order_shape == PAIR`，则后续 compare layer 还会继续使用
`get_pair_state(...)` 来确认当前 pair snapshot 是否存在并可与 saved state 比较。

这里的 parse 结果只是 strategy 后续 compare / decide 的输入，不直接决定最终动作。

## 4. Step 2: Compare Live Vs Expected Pair State

compare layer 的职责是：

- 将 parse 得到的 current live structure 与 saved expected pair state 做当前 pair 语义下的比较
- 显式区分：
  - fill-driven rebuild candidate
  - `PAIR` keep candidate
  - single-sided branch candidate
  - abnormal fallback candidate

这里的 compare layer 仍然是 current pair runtime truth。
它只是让当前 pair strategy 更容易映射到 ladder-first schema language，
并不表示 ladder `M = 1` 吸收已经完成。

### 4.1 Fill-Driven Rebuild Candidate

fill-driven rebuild 拥有当前 strategy 的最高优先级。

#### Saved `PAIR`

当 saved `mode == PAIR` 时：

- 若当前 live shape == `SELL_ONLY`，表示 buy 已成交  
  返回 `("rebuild", state["buy_price"])`
- 若当前 live shape == `BUY_ONLY`，表示 sell 已成交  
  返回 `("rebuild", state["sell_price"])`

#### Saved `BUY_ONLY`

当 saved `mode == BUY_ONLY` 且当前 `open orders == 0` 时：

- 视为 buy residual completion
- 返回 `("rebuild", state["buy_price"])`

#### Saved `SELL_ONLY`

当 saved `mode == SELL_ONLY` 且当前 `open orders == 0` 时：

- 视为 sell residual completion
- 返回 `("rebuild", state["sell_price"])`

若命中上述任一路径，则 compare result 必须把当前状态标记为
fill-driven rebuild candidate。

### 4.2 `PAIR` Keep Candidate

只有同时满足以下条件时，compare result 才接受 `PAIR` keep candidate：

- saved `mode == PAIR`
- 当前 live shape == `PAIR`
- `get_pair_state(...)` 成功
- 当前 `buy_price` 严格等于 saved `buy_price`
- 当前 `sell_price` 严格等于 saved `sell_price`

若不成立，则 compare layer 不得把当前状态标记为 `PAIR` keep candidate。

### 4.3 Single-Sided Branch Candidate

单边 residual branch 只在 saved `mode == BUY_ONLY` 或 `SELL_ONLY` 时，
作为 compare result 中的合法 single-sided branch candidate 被表达。

#### `BUY_ONLY` branch

只有同时满足以下条件时，才接受 `BUY_ONLY` branch：

- saved `mode == BUY_ONLY`
- 当前 live shape == `BUY_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单必须是买单

在此基础上：

- 若未触发 anchor 子合同，则返回 `("keep", None)`
- 若触发 anchor 子合同，则返回其定义的 stale rebuild 结果

#### `SELL_ONLY` branch

只有同时满足以下条件时，才接受 `SELL_ONLY` branch：

- saved `mode == SELL_ONLY`
- 当前 live shape == `SELL_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单必须是卖单

在当前实现中：

- `SELL_ONLY` 只允许 `keep`
- `SELL_ONLY` 不执行 anchor break 检查

设计约束说明：

- anchor break 仅适用于 `BUY_ONLY` residual
- 当前 strategy 不允许对 `SELL_ONLY` residual 做 re-anchor
- 因此 `SELL_ONLY` branch 不存在 stale rebuild 分支

若上述 branch guard 不成立，则 compare layer 不得把当前状态标记为合法单边分支。

## 5. Step 3: Decide Action From Compare Result

decide layer 只负责读取 compare result，并按固定优先级输出唯一动作。

### 5.1 Fill-Driven Rebuild

只要 compare result 命中 fill-driven rebuild candidate：

- strategy 必须返回对应的 `("rebuild", reference_price)`
- 不得继续评估 keep
- 不得返回 abnormal

### 5.2 `PAIR` Keep

只有在未命中 fill-driven rebuild 时，且 compare result 命中 `PAIR` keep candidate：

- 返回 `("keep", None)`

### 5.3 Single-Sided Branch

只有在未命中 fill-driven rebuild、也未命中 `PAIR` keep 时，
才允许进入单边 residual branch：

- `BUY_ONLY` branch：先检查 anchor 子合同，再决定 `keep` 或 stale rebuild
- `SELL_ONLY` branch：只允许 `keep`

### 5.4 Abnormal Fallback

凡是不属于以下已接受路径的 live state，一律返回：

- `("abnormal", None)`

已接受路径仅包括：

- fill-driven rebuild
- `PAIR` keep
- 合法 `BUY_ONLY` branch
- 合法 `SELL_ONLY` branch
- `BUY_ONLY` stale rebuild

strategy 不负责部分修复，不负责兜底恢复，也不负责把 abnormal 强行转成 rebuild。

## 6. Anchor Subcontract Boundary

anchor 子合同只在以下位置被评估：

- 单边 residual branch 内部
- 且仅限 `BUY_ONLY` branch

它永远不能：

- 早于 fill-driven rebuild
- 覆盖 `PAIR` keep
- 覆盖 `SELL_ONLY` keep
- 处理任意 abnormal 订单形态

## 7. Out Of Scope

本文档不定义：

- engine shell startup / shutdown
- rebuild 的执行顺序
- 下单、撤单、清理与重试
- future rolling ladder / fixed-M moving window strategy
