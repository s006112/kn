# Pair Strategy Contract

本文档只描述当前 single-pair strategy 中成立的策略规则。

它依赖父合同 `contract_grid_engine.md` 已定义的 engine state model、open-order shape model、主循环判定顺序、rebuild success / failure 与 abnormal / exit contract。

本文档位于 engine contract 与 anchor 子合同之间：

- 父合同：`contract_grid_engine.md`
- 子合同：`contract_strategy_anchor.md`

## 1. Purpose

当前 pair strategy 的职责是：

- 在 engine 已接受的 `PAIR` / `BUY_ONLY` / `SELL_ONLY` saved state 之上
  定义 single-pair strategy 自己的 keep / rebuild / abnormal 接受规则
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

本文档不定义 engine 通用循环，也不定义未来 rolling ladder 或 multi-level strategy。

## 2. Strategy Scope

当前 pair strategy 只适用于以下对象：

- saved state 只来自当前 single-pair implementation
- live orders 只围绕一个 buy price 与一个 sell price 的配对或残单展开
- 单边 residual 只允许是单笔合法买残单或单笔合法卖残单

当前 strategy 不处理：

- 多层 grid
- 滑动窗口 ladder
- 多于 `1 BUY + 1 SELL` 的合法策略状态
- 任意 abnormal live orders 的部分修复

## 3. Strategy State Interpretation

在父合同允许的三个 `mode` 中，当前 pair strategy 的语义解释为：

### 3.1 `PAIR`

表示当前策略承认：

- 保存态中应存在一组严格有效的 buy / sell pair
- 当前 live orders 若要继续 `keep`，必须仍然是同一组价格上的严格 pair
- 若其中一侧成交并只剩另一侧 residual，则进入 fill-driven rebuild 判定

### 3.2 `BUY_ONLY`

表示当前策略承认：

- 上一轮下单或重建后，只剩单笔 buy residual 属于合法中间态
- 该中间态可以继续 `keep`
- 若该 residual 完成成交，则进入 fill completion rebuild
- 若该 residual 已不再符合 anchor 子合同，则进入 stale rebuild

### 3.3 `SELL_ONLY`

表示当前策略承认：

- 上一轮下单或重建后，只剩单笔 sell residual 属于合法中间态
- 该中间态可以继续 `keep`
- 若该 residual 完成成交，则进入 fill completion rebuild
- 当前实现中，`SELL_ONLY` 不执行 anchor break 检查

## 4. Strategy Decision Order

当前 pair strategy 必须严格按以下顺序判定：

1. 优先检查 fill-driven rebuild
2. 再检查 `PAIR` keep
3. 再检查单边 residual 分支
4. 以上都不满足则返回 `abnormal`

其中：

- 单边 residual 分支只在 saved `mode` 为 `BUY_ONLY` 或 `SELL_ONLY` 时评估
- 单边 residual 分支永远不能覆盖 fill-driven rebuild
- 单边 residual 分支永远晚于 `PAIR` keep

## 5. Fill-Driven Rebuild Semantics

当前 pair strategy 接受以下 fill-driven rebuild 路径：

### 5.1 Saved `PAIR`

当 saved `mode == PAIR` 时：

- 当前 live shape 为 `SELL_ONLY`，表示 buy side 已成交
- 当前 live shape 为 `BUY_ONLY`，表示 sell side 已成交

此时策略必须返回：

- `("rebuild", state["buy_price"])`，当 buy 已成交
- `("rebuild", state["sell_price"])`，当 sell 已成交

这里的 `reference_price` 必须沿用“已成交那一侧”的保存价格。

### 5.2 Saved `BUY_ONLY`

当 saved `mode == BUY_ONLY` 且当前 `open orders == 0` 时：

- 视为合法 residual completion
- 必须返回 `("rebuild", state["buy_price"])`

### 5.3 Saved `SELL_ONLY`

当 saved `mode == SELL_ONLY` 且当前 `open orders == 0` 时：

- 视为合法 residual completion
- 必须返回 `("rebuild", state["sell_price"])`

只要命中以上路径，就不得落入 keep，也不得落入 abnormal。

## 6. `PAIR` Keep Contract

只有同时满足以下条件时，当前 pair strategy 才接受 `PAIR` keep：

- saved `mode == PAIR`
- 当前 live shape 仍为 `PAIR`
- `get_pair_state(...)` 成功
- 当前 `buy_price` 严格等于 saved `buy_price`
- 当前 `sell_price` 严格等于 saved `sell_price`

否则 `PAIR` 不得 `keep`。

## 7. Single-Sided Branch Contract

### 7.1 `BUY_ONLY` branch

只有同时满足以下条件时，当前 strategy 才接受 `BUY_ONLY` 分支：

- saved `mode == BUY_ONLY`
- 当前 live shape == `BUY_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单必须是买单

在此基础上：

- 若未触发 anchor 子合同，则返回 `("keep", None)`
- 若触发 anchor 子合同，则返回其定义的 rebuild 结果

### 7.2 `SELL_ONLY` branch

只有同时满足以下条件时，当前 strategy 才接受 `SELL_ONLY` 分支：

- saved `mode == SELL_ONLY`
- 当前 live shape == `SELL_ONLY`
- 当前恰好只有 `1` 笔订单
- 该订单必须是卖单

在当前实现中：

- `SELL_ONLY` 只允许 `keep`
- `SELL_ONLY` 不执行 anchor break 检查

若上述条件不成立，则 `SELL_ONLY` 分支不得被接受。

## 8. Anchor Subcontract Boundary

当前 pair strategy 将 `BUY_ONLY` residual stale 检查下放给子合同 `contract_strategy_anchor.md`。

该子合同只负责：

- `BUY_ONLY` residual stale 的触发条件
- stale rebuild 的动作语义
- stale rebuild 的优先级和非目标

当前 pair strategy 自身只保证：

- anchor 子合同只在单边分支内部评估
- anchor 子合同永远晚于 fill-driven rebuild
- anchor 子合同永远晚于 shape 校验
- anchor 子合同不能覆盖 `PAIR` keep

## 9. Abnormal Boundary

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

## 10. Logging Boundary

当前 pair strategy 只定义以下日志语义边界：

- `PAIR` keep 使用 pair keep 日志
- `BUY_ONLY` keep 使用 buy-only keep 日志
- `SELL_ONLY` keep 使用 sell-only keep 日志
- fill-driven rebuild 使用 fill completion / fill detected 日志
- anchor stale rebuild 的专属日志由 anchor 子合同定义

具体日志文本、限流实现与输出方式不由本文档定义。

## 11. Out Of Scope

本文档不定义：

- engine 通用 state model 与 rebuild failure contract
- exchange 下单、撤单、清理与重试
- 参考价构造细节
- budget sizing 规则
- rolling ladder / moving window / fixed-M multi-level strategy
- 当前实现之外的未来策略扩展