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

1. classify 当前 open-order shape
2. 优先检查 fill-driven rebuild
3. 再检查 `PAIR` keep
4. 再检查单边 residual branch
5. 以上都不满足则返回 `abnormal`

## 3. Step 1: Shape Classification

strategy 首先读取当前 live orders，并将其分类为：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`
- `ABNORMAL`

这里的 shape classification 只是 strategy 后续判定的输入，不直接决定最终动作。

## 4. Step 2: Fill-Driven Rebuild

fill-driven rebuild 拥有当前 strategy 的最高优先级。

### 4.1 Saved `PAIR`

当 saved `mode == PAIR` 时：

- 若当前 live shape == `SELL_ONLY`，表示 buy 已成交  
  返回 `("rebuild", state["buy_price"])`
- 若当前 live shape == `BUY_ONLY`，表示 sell 已成交  
  返回 `("rebuild", state["sell_price"])`

### 4.2 Saved `BUY_ONLY`

当 saved `mode == BUY_ONLY` 且当前 `open orders == 0` 时：

- 视为 buy residual completion
- 返回 `("rebuild", state["buy_price"])`

### 4.3 Saved `SELL_ONLY`

当 saved `mode == SELL_ONLY` 且当前 `open orders == 0` 时：

- 视为 sell residual completion
- 返回 `("rebuild", state["sell_price"])`

只要命中 fill-driven rebuild，就不得继续评估 keep，也不得返回 abnormal。

## 5. Step 3: `PAIR` Keep

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

## 6. Step 4: Single-Sided Branch

单边 residual branch 只在 saved `mode == BUY_ONLY` 或 `SELL_ONLY` 时评估。

### 6.1 `BUY_ONLY` branch

只有同时满足以下条件时，才接受 `BUY_ONLY` branch：

- saved `mode == BUY_ONLY`
- 当前 live shape == `BUY_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单必须是买单

在此基础上：

- 若未触发 anchor 子合同，则返回 `("keep", None)`
- 若触发 anchor 子合同，则返回其定义的 stale rebuild 结果

### 6.2 `SELL_ONLY` branch

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

## 7. Step 5: Abnormal Fallback

凡是不属于以下已接受路径的 live state，一律返回：

- `("abnormal", None)`

已接受路径仅包括：

- fill-driven rebuild
- `PAIR` keep
- 合法 `BUY_ONLY` branch
- 合法 `SELL_ONLY` branch
- `BUY_ONLY` stale rebuild

strategy 不负责部分修复，不负责兜底恢复，也不负责把 abnormal 强行转成 rebuild。

## 8. Anchor Subcontract Boundary

anchor 子合同只在以下位置被评估：

- 单边 residual branch 内部
- 且仅限 `BUY_ONLY` branch

它永远不能：

- 早于 fill-driven rebuild
- 覆盖 `PAIR` keep
- 覆盖 `SELL_ONLY` keep
- 处理任意 abnormal 订单形态

## 9. Out Of Scope

本文档不定义：

- engine shell startup / shutdown
- rebuild 的执行顺序
- 下单、撤单、清理与重试
- future rolling ladder / fixed-M moving window strategy