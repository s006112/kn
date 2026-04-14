# Pair Strategy Contract

## 1. Purpose

定义当前 single-pair strategy 的唯一决策语义。

strategy 的职责是：

- 基于 engine 提供的 live orders 与 saved state
- 输出唯一决策：

  - `("keep", None)`
  - `("rebuild", reference_price)`
  - `("rebuild", None)`
  - `("abnormal", None)`

strategy 不关心：

- transport
- gateway retry
- engine lifecycle

## 2. Scope

当前 strategy 只适用于：

- 单组 pair
- 或其合法单边 residual

约束：

- live orders 只允许：
  - `1 BUY + 1 SELL`
  - `1 BUY`
  - `1 SELL`
- 不处理：
  - 多层 grid
  - 多单
  - 部分修复 abnormal

## 3. Decision Contract

决策顺序固定为：

1. classify shape  
2. fill-driven rebuild  
3. `PAIR` keep  
4. single-sided branch  
5. abnormal  

约束：

- 每轮只返回一个 action
- decision 只依赖当前输入
- 顺序不可改变

## 4. Shape & Pair Validity

### Shape 分类

- `PAIR` → 1 BUY + 1 SELL 且为合法 pair
- `BUY_ONLY` → 仅 1 BUY
- `SELL_ONLY` → 仅 1 SELL
- 其他 → `ABNORMAL`

### Valid Pair 条件

必须满足：

- 恰好 1 BUY + 1 SELL
- buy < sell
- price > 0
- gap 匹配 expected gap
- 所有价格比较必须使用离散化比较

返回：

- `mode`
- `buy_price`
- `sell_price`
- `pair_center_price`

## 5. Fill-Driven Rebuild

优先级最高。

### saved = PAIR

- SELL_ONLY → `rebuild(state.buy_price)`
- BUY_ONLY → `rebuild(state.sell_price)`

### saved = BUY_ONLY / SELL_ONLY

- orders == 0 → `rebuild(saved price)`

约束：

- 必须使用“已成交那一侧”的 price
- 不允许改用 mid 或其他 anchor

## 6. Keep Contract

### PAIR keep

必须同时满足：

- saved = PAIR
- shape = PAIR
- pair 有效
- buy / sell 与 saved 相等（离散化比较）

→ `keep`

否则不得 keep

## 7. Single-Sided Branch

### BUY_ONLY

必须：

- saved = BUY_ONLY
- shape = BUY_ONLY
- 仅 1 BUY

行为：

- 若未触发 Anchor Break → `keep`
- 若触发 → `rebuild(None)`

### SELL_ONLY

必须：

- saved = SELL_ONLY
- shape = SELL_ONLY
- 仅 1 SELL

行为：

- 只允许 `keep`

## 8. Anchor Break

只适用于 BUY_ONLY。

### Trigger

必须同时满足：

- `REANCHOR_BREAK == True`
- saved = BUY_ONLY
- shape = BUY_ONLY
- 仅 1 BUY
- BTC mid 可用
- distance ≥ `REANCHOR_BREAK_STEPS * GRID_STEP`

distance 使用离散化比较。

### Action

→ `("rebuild", None)`

语义：

- 放弃旧 residual
- 使用 fresh anchor rebuild

### Priority

- 晚于 fill-driven rebuild
- 覆盖 BUY_ONLY keep

## 9. Abnormal Boundary

以下情况必须 abnormal：

- shape 不合法
- saved 与 shape 不一致
- 单边数量 ≠ 1
- 出现错误方向 residual
- 任意未命中 accepted path

strategy 不做修复。

## 10. Invariants

必须满足：

- decision 顺序固定
- fill-driven rebuild 优先级最高
- 关键价格比较必须离散化
- 每轮仅一个 action

### Sizing

- invariant = **same notional**
- 非 same base size

即：

- buy / sell size 可不同
- 不参与 pair validity 判断