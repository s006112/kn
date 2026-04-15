# Pair Strategy Contract

## 1. Purpose

本合同定义当前 single-pair strategy 的唯一决策语义。

strategy 的职责是：

- 基于 engine 提供的当前 `saved_state`
- 基于当前 `live orders`
- 在 `BUY_ONLY` 分支按需消费当前 `BTC mid`
- 输出唯一决策：

  - `("keep", None)`
  - `("rebuild", reference_price)`
  - `("rebuild", None)`
  - `("abnormal", None)`

strategy 不关心：

- REST client 细节
- gateway retry
- engine lifecycle
- 异常统一收口

## 2. Scope

当前 strategy 只适用于：

- 单组 pair
- 或其合法单边 residual

live orders 只允许处理：

- `1 BUY + 1 SELL`
- `1 BUY`
- `1 SELL`
- `0 order` 仅在 saved 为单边 residual 时作为 fill-complete 路径处理

不处理：

- 多层 grid
- 多组 pair
- 多单并存
- abnormal 自动修复

## 3. Decision Contract

每轮决策只允许返回：

- `("keep", None)`
- `("rebuild", reference_price)`
- `("rebuild", None)`
- `("abnormal", None)`

约束：

- 每轮只返回一个 action
- decision 只依赖当前输入
- 顺序不可改变
- 同一输入必须产生同一输出

固定顺序为：

1. classify shape
2. fill-driven rebuild
3. `PAIR` keep
4. single-sided branch
5. abnormal

## 4. Shape Contract

当前 live orders 的 shape 只允许分类为：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`
- `ABNORMAL`

分类规则：

### `PAIR`

必须满足：

- 恰好 2 张单
- 其中 1 BUY + 1 SELL
- 可被识别为合法 pair

### `BUY_ONLY`

必须满足：

- 恰好 1 张单
- 且该单为 BUY

### `SELL_ONLY`

必须满足：

- 恰好 1 张单
- 且该单为 SELL

### `ABNORMAL`

其他一切情况都必须视为 abnormal，包括但不限于：

- 0 张单且 saved 不是单边 residual fill-complete 路径
- 2 张单但不是合法 pair
- 超过 2 张单
- 非法 side
- 任意未命中 accepted path 的 shape

## 5. Pair Validity Contract

合法 pair 必须满足：

- 恰好 1 BUY + 1 SELL
- `buy_price > 0`
- `sell_price > 0`
- `buy_price < sell_price`
- `pair_center_price > 0`
- `gap == (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP`
- 所有关键价格判定必须使用离散化比较

返回的 canonical pair state 必须包括：

- `mode`
- `buy_price`
- `sell_price`
- `pair_center_price`

其中：

- `buy_price` 与 `sell_price` 必须是 normalized canonical price
- `pair_center_price` 必须是两侧价格中点的 normalized canonical price

## 6. Bootstrap Acceptance Contract

bootstrap 阶段 strategy 识别规则：

- 仅当 live orders 为合法 `PAIR` 时返回有效 state。
- 严禁在 bootstrap 阶段接受 `BUY_ONLY` 或 `SELL_ONLY`。

语义：
- 启动时的单边残余不再被视为可继承状态，必须通过 engine 执行 rebuild 以恢复双边网格。

## 7. Fill-Driven Rebuild Contract

fill-driven rebuild 的优先级最高。

### 当 saved = `PAIR`

若当前 shape 为：

- `SELL_ONLY`  
  → 视为 BUY filled  
  → `("rebuild", saved.buy_price)`

- `BUY_ONLY`  
  → 视为 SELL filled  
  → `("rebuild", saved.sell_price)`

语义：

- 必须使用刚成交那一侧的已知 price 作为 rebuild anchor
- 不允许改用 mid
- 不允许改用 fresh reference price

### 当 saved = `BUY_ONLY`

若当前 `orders == 0`：

- 视为 residual BUY fill complete
- `("rebuild", saved.buy_price)`

### 当 saved = `SELL_ONLY`

若当前 `orders == 0`：

- 视为 residual SELL fill complete
- `("rebuild", saved.sell_price)`

约束：

- fill-driven rebuild 必须先于 keep 判定
- fill-driven rebuild 必须先于 anchor break 判定
- fill-driven rebuild 必须使用 saved canonical price

## 8. Keep Contract

### `PAIR` keep

只有在以下条件全部成立时才允许 `keep`：

- `saved.mode == PAIR`
- `shape == PAIR`
- 当前 live orders 可识别为合法 pair
- 当前 `buy_price == saved.buy_price`（离散化比较）
- 当前 `sell_price == saved.sell_price`（离散化比较）

满足时：

- `("keep", None)`

否则：

- 不得 `keep`

### `BUY_ONLY` keep

只有在以下条件全部成立时才允许 `keep`：

- `saved.mode == BUY_ONLY`
- `shape == BUY_ONLY`
- 当前恰好 1 张 BUY
- 未触发 anchor break

满足时：

- `("keep", None)`

### `SELL_ONLY` keep

只有在以下条件全部成立时才允许 `keep`：

- `saved.mode == SELL_ONLY`
- `shape == SELL_ONLY`
- 当前恰好 1 张 SELL

满足时：

- `("keep", None)`

## 9. Anchor Break Contract

anchor break 只适用于 `BUY_ONLY`。

### Trigger

必须同时满足：

- `saved.mode == BUY_ONLY`
- `shape == BUY_ONLY`
- 当前恰好 1 张 BUY
- `REANCHOR_BREAK == True`
- `BTC mid` 可用且大于 0
- `distance >= REANCHOR_BREAK_STEPS * GRID_STEP`

其中：

- `distance` 必须使用离散化比较
- `distance` 比较的是 `BTC mid` 与当前 BUY order price

### Action

满足时：

- `("rebuild", None)`

语义：

- 放弃旧 residual anchor
- 改为使用 fresh reference price rebuild

### Priority

anchor break 的优先级：

- 晚于 fill-driven rebuild
- 高于 `BUY_ONLY keep`

## 10. Abnormal Boundary

以下情况必须返回 abnormal：

- shape 非法
- saved 与当前 shape 不匹配且未命中 accepted transition
- `PAIR` 下当前 orders 不是合法 pair 且未命中 fill-driven rebuild
- `BUY_ONLY` 下当前不是单一 BUY 且未命中 fill-complete
- `SELL_ONLY` 下当前不是单一 SELL 且未命中 fill-complete
- 任意未命中 accepted path 的输入

strategy 不做自动修复。

## 11. Price Comparison Contract

以下关键判断必须使用离散化比较：

- `buy_price == saved.buy_price`
- `sell_price == saved.sell_price`
- pair gap 是否匹配 expected gap
- anchor break threshold distance

不允许：

- 关键 pair 判定直接依赖 float equality
- gap 判定直接依赖未离散化浮点减法
- anchor break 直接比较原始浮点距离

## 12. Invariants

strategy 必须满足：

- decision 顺序固定
- fill-driven rebuild 优先级最高
- 每轮仅一个 action
- decision 只依赖当前输入
- 关键价格比较必须离散化
- `PAIR` keep 必须同时比较 buy / sell 两侧
- `BUY_ONLY` 不得依赖 `sell_price`
- `SELL_ONLY` 不得依赖 `buy_price`

### Sizing Invariant

当前 sizing invariant 为：

- **same notional**
- 不是 same base size

即：

- buy / sell size 可以不同
- size 不参与 pair validity 判断