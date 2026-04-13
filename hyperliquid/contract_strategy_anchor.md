# Anchor Break Contract

本文档只描述当前 pair strategy 中的 anchor break / 单边 residual 子合同。

它依赖父合同 `contract_grid_engine.md` 已定义的 state model、order shape model、主循环顺序、fill-driven rebuild contract、rebuild contract 与 abnormal contract，并作为 `contract_strategy_pair.md` 的子合同存在。

## 1. Purpose

Anchor Break 是“合法单边 residual order”上的兜底分支。

它的作用是：当系统当前仍处于有效 `BUY_ONLY` 单边模式，且唯一 residual buy order 与当前 BTC mid 的偏离已经足够大时，不再继续 `keep`，而是改走 `rebuild`。

它只约束“当前 BTC mid 值满足什么条件时触发 stale rebuild”，不规定该当前值由 engine 通过何种 transport / infra 提供。

它不是通用恢复机制，也不处理任意 abnormal 订单形态。

## 2. Scope Limit

Anchor Break 只对以下对象有效：

- saved `mode` 为 `BUY_ONLY`
- 当前 `order_shape` 与 saved 单边模式完全一致
- 当前恰好只有 `1` 笔 residual order
- 该 residual order 必须是买单

对于 `SELL_ONLY`，anchor break 不生效；`SELL_ONLY` 只允许走自身 keep / fill completion / abnormal 的父合同路径。

除此之外，anchor break 不生效。

## 3. Single-Sided Keep

### 3.1 `BUY_ONLY` keep

只有同时满足以下条件时，`BUY_ONLY` 才继续 `keep`：

- saved `mode` 是 `BUY_ONLY`
- 当前分类结果也是 `BUY_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单的 `side` 必须是买单
- 没有触发 anchor break

### 3.2 `SELL_ONLY` keep

只有同时满足以下条件时，`SELL_ONLY` 才继续 `keep`：

- saved `mode` 是 `SELL_ONLY`
- 当前分类结果也是 `SELL_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单的 `side` 必须是卖单

`SELL_ONLY` 当前实现中不执行 anchor break 检查。
只要上述条件成立，即返回 `keep`。
若上述条件不成立，单边分支不得 `keep`。

## 4. Trigger Conditions

只有同时满足以下条件时，anchor break 才会触发：

- `REANCHOR_BREAK == True`
- saved `mode` 是 `BUY_ONLY`
- 当前分类结果与 saved 单边模式完全一致
- 当前 `open orders` 数量恰好为 `1`
- 该 residual order 是买单
- BTC mid 可以读取，且大于 `0`
- residual 与 BTC mid 的距离达到 `REANCHOR_BREAK_STEPS * GRID_STEP`

距离判定规则：

- buy residual: `btc_mid - order_price >= REANCHOR_BREAK_STEPS * GRID_STEP`

当前实现中不存在 `SELL_ONLY` anchor break trigger。
只要任一 guard 不满足，就不能触发 anchor break。

## 5. Action Semantics

当 anchor break 触发时，单边分支返回 `("rebuild", None)`。

这表示后续 `rebuild(...)` 会：

- 先取消当前 residual order
- 因为显式传入的是 `reference_price=None`，所以改由 `get_mid_reference_price()` 推导新的锚点
- 用这个 fresh mid-grid anchor 重新下单

它不是沿用旧 residual 价格的 fill-driven rebuild。

## 6. Priority

Anchor Break 的优先级关系必须满足：

- 它只在单边分支内部评估
- 它永远晚于 fill-driven rebuild
- 它永远晚于 order shape 校验
- 一旦触发，它覆盖当前实现中的 `BUY_ONLY` keep

## 7. Non-Goals

Anchor Break 不负责：

- 覆盖或替代 fill-driven rebuild
- 修复 mode / shape mismatch
- 修复多单、多余残单或无效 pair
- 接受不合法的单边 residual
- 定义 engine 级 rebuild success / failure
