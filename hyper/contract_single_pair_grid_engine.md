# Single Pair Grid Engine Contract

## 1. Purpose

本合同定义当前 single-pair grid engine 的唯一运行边界与唯一策略语义。

当前系统是：

- 单组 pair
- 纯 REST / polling
- contract-driven state machine

系统目标：

- 持有唯一 saved state
- 通过固定 polling loop 读取当前 live input
- 在 accepted boundary 内做唯一决策
- 执行 keep / rebuild / abnormal
- 仅在得到有效新 state 后继续运行
- 对未知异常统一收口并退出

本合同是当前项目的单一语义源。
实现不得在本合同之外扩展隐含状态机语义。

## 2. Ownership and Boundaries

### Engine

负责：

- 持有唯一 saved state
- 驱动 bootstrap 与固定 polling loop
- 组织 live input
- 调用 decision
- 执行 keep / rebuild / abnormal
- 对运行期异常统一收口

不负责：

- REST 读取细节
- retry 细节
- 下单结果解析
- 策略判定

### Strategy

负责：

- 基于当前输入做唯一决策
- 定义 pair / single-sided / abnormal 的 accepted path
- 定义 fill-driven rebuild、keep、anchor break 的语义与优先级

不负责：

- IO
- retry
- lifecycle
- 异常收口
- 下单执行

### Gateway

是唯一读取边界，负责：

- 通过 REST 读取 open orders 与 BTC mids
- 归一化 order shape
- 统一 price normalization
- 对暂时性 infra read failure 做有限重试

不负责：

- 状态机
- 策略决策
- saved state 管理
- 下单执行

### Execution

负责：

- cleanup 现有挂单
- wait until no open orders
- 构造 pair order actions
- 提交 buy / sell limit orders
- 根据 placement result 返回新 state 或失败

不负责：

- 策略决策
- IO retry
- 主循环控制
- 异常统一收口

## 3. Scope

当前合同只适用于：

- 单组 pair
- 或其合法单边 residual
- 纯 REST / polling 主循环

本合同不定义：

- 多层 grid
- 多组 pair
- websocket / event-driven input model
- transport abstraction
- 自动异常修复
- placement 后更复杂补救策略
- 多币对并发状态机
- 更高层 ladder / rolling / portfolio 语义

## 4. Accepted State Model

saved state 只允许为：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`

`ABNORMAL` 不允许作为继续运行的 saved state，只允许作为失败结果或退出结果。

允许以下 state shape：

### `PAIR` live state

用于 bootstrap 直接识别合法 pair：

- `mode`
- `buy_price`
- `sell_price`
- `pair_center_price`

其中 `pair_center_price` 表示当前 live pair 的 canonical center。

### `PAIR` rebuild state

用于 rebuild 成功后返回：

- `mode`
- `buy_price`
- `sell_price`
- `reference_price`

其中 `reference_price` 表示本次 rebuild 使用的 anchor。

### `BUY_ONLY` rebuild state

- `mode`
- `buy_price`
- `reference_price`

### `SELL_ONLY` rebuild state

- `mode`
- `sell_price`
- `reference_price`

约束：

- `pair_center_price` 表示 live pair center
- `reference_price` 表示 rebuild anchor
- 两者不可混用
- `BUY_ONLY` 不得依赖 `sell_price`
- `SELL_ONLY` 不得依赖 `buy_price`

## 5. Live Input Boundary

每轮 decision 只能基于当前输入：

- 当前 `saved_state`
- 当前 `live orders`
- 当前按需读取的 `BTC mid`

约束：

- decision 只依赖当前输入，不依赖历史事件序列
- decision 不得直接做 IO
- engine 不维护事件驱动内存态、runtime input 容器或额外 rebuild 运行态标志

## 6. Shape and Pair Validity Contract

当前 live orders 的 shape 只允许分类为：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`
- `ABNORMAL`

### `PAIR`

必须满足：

- 恰好 2 张单
- 其中 1 BUY + 1 SELL
- 可被识别为合法 pair

合法 pair 还必须满足：

- `buy_price > 0`
- `sell_price > 0`
- `buy_price < sell_price`
- `pair_center_price > 0`
- `gap == (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP`
- 所有关键价格判定必须使用离散化比较

canonical pair state 必须包括：

- `mode`
- `buy_price`
- `sell_price`
- `pair_center_price`

其中：

- `buy_price` 与 `sell_price` 必须是 normalized canonical price
- `pair_center_price` 必须是两侧价格中点的 normalized canonical price

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

- 0 张单且未命中单边 residual fill-complete 路径
- 2 张单但不是合法 pair
- 超过 2 张单
- 非法 side
- 任意未命中 accepted path 的 shape

## 7. Bootstrap Contract

engine 启动时必须：

1. 初始化 client
2. 读取当前 open orders
3. 输出当前 open orders 摘要
4. 尝试识别合法 bootstrap state
5. 识别失败则执行 rebuild

bootstrap 只允许接受：

- 合法 `PAIR`

约束：

- bootstrap 不接受 `BUY_ONLY` / `SELL_ONLY`
- 启动时单边 residual 必须通过 rebuild 清理并重建
- 若无法得到有效 state 且 rebuild 失败，则必须退出

语义分工：

- strategy 只负责识别当前 live orders 是否为合法 `PAIR`
- engine 负责决定 bootstrap 是否接受该结果

## 8. Decision Contract

每轮 decision 只允许返回：

- `("keep", None)`
- `("rebuild", reference_price)`
- `("rebuild", None)`
- `("abnormal", None)`

约束：

- 同一输入必须产生同一输出
- 每轮只允许一个 action
- decision 顺序固定为：

1. classify shape
2. fill-driven rebuild
3. `PAIR` keep
4. single-sided branch
5. abnormal

## 9. Fill-Driven Rebuild Contract

fill-driven rebuild 优先级最高。

### 当 saved = `PAIR`

若当前 shape 为：

- `SELL_ONLY` → 视为 BUY filled → `("rebuild", saved.buy_price)`
- `BUY_ONLY` → 视为 SELL filled → `("rebuild", saved.sell_price)`

约束：

- 必须使用刚成交那一侧的 canonical price 作为 rebuild anchor
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

- fill-driven rebuild 必须先于 keep
- fill-driven rebuild 必须先于 anchor break
- fill-driven rebuild 必须使用 saved canonical price

## 10. Keep and Anchor Break Contract

### `PAIR` keep

只有在以下条件全部成立时才允许 `keep`：

- `saved.mode == PAIR`
- `shape == PAIR`
- 当前 live orders 可识别为合法 pair
- 当前 `buy_price == saved.buy_price`
- 当前 `sell_price == saved.sell_price`

以上比较必须使用离散化比较。

### `BUY_ONLY` keep

只有在以下条件全部成立时才允许 `keep`：

- `saved.mode == BUY_ONLY`
- `shape == BUY_ONLY`
- 当前恰好 1 张 BUY
- 未触发 anchor break

### `SELL_ONLY` keep

只有在以下条件全部成立时才允许 `keep`：

- `saved.mode == SELL_ONLY`
- `shape == SELL_ONLY`
- 当前恰好 1 张 SELL

### Anchor Break

anchor break 只适用于 `BUY_ONLY`，且必须同时满足：

- `saved.mode == BUY_ONLY`
- `shape == BUY_ONLY`
- 当前恰好 1 张 BUY
- `REANCHOR_BREAK == True`
- `BTC mid` 可用且大于 0
- `distance >= REANCHOR_BREAK_STEPS * GRID_STEP`

约束：

- `distance` 必须使用离散化比较
- 比较对象是 `BTC mid` 与当前 BUY order price
- 触发时返回 `("rebuild", None)`
- 语义是放弃旧 residual anchor，改用 fresh reference price rebuild
- 优先级晚于 fill-driven rebuild，高于 `BUY_ONLY keep`

若 `BTC mid` 不可用、无效或读取失败：

- 不得触发 anchor break
- 不得改用推测值或历史值

## 11. Main Loop Contract

主循环必须是固定 polling loop。

每轮必须执行：

1. sleep 固定轮询间隔
2. 读取当前 open orders
3. 仅当 `saved_state.mode == BUY_ONLY` 时按需读取 BTC mid
4. 调用 decision
5. 执行 `keep / rebuild / abnormal`

### Keep

当 decision 返回 `("keep", None)` 时：

- 不改变 saved state
- 不执行任何 order operation
- 继续下一轮 polling

### Rebuild

当 decision 返回 `("rebuild", reference_price)` 或 `("rebuild", None)` 时：

- 使用当前 live orders 进入 rebuild
- 如有旧挂单，先 cleanup
- cleanup 成功后才允许 placement
- 若 `reference_price is None`，则读取 fresh reference price
- placement 成功后仅在得到有效 state 时更新 saved state
- rebuild 失败则退出

### Abnormal

当 decision 返回 `("abnormal", None)` 时：

- 输出当前 open orders 摘要
- 记录 abnormal
- 立即退出
- 不做自动修复

## 12. Gateway Contract

gateway 是 engine 的唯一读取边界。

职责：

- 通过 REST 读取 open orders 与 BTC mids
- 归一化 order shape
- 统一 price normalization
- 对短暂性 infra read failure 做有限重试

约束：

- decision 不得直接依赖底层 client
- retry 只允许存在于读取侧
- retry 不得扩散到 decision 层
- gateway 可以抛出异常
- engine 必须统一收口，不得在未知输入上继续运行
- 非法 side 必须视为非法输入，不得进入 strategy decision
- canonical order shape 至少应提供 strategy / execution 所需字段

## 13. Execution Contract

execution 负责：

- cleanup 现有挂单
- wait until no open orders
- 构造 pair order actions
- 提交 buy / sell limit orders
- 根据 placement result 分类返回新 state 或失败

### Cleanup

cleanup 语义必须为：

- 无 open orders 则直接成功
- 有 open orders 则先 bulk cancel
- 之后轮询确认 no open orders
- 若确认失败，则 rebuild 失败

约束：

- cleanup 输入订单必须可用于 cancel
- 若缺少 cancel 所需字段，则视为 cleanup failure
- cleanup failure 不得伪装为成功

### Placement Result Classification

下单结果只允许分为：

- `ok`
- `insufficient_spot_balance`
- `error`

rebuild 后新状态只允许是：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`
- `None`

约束：

- rebuild 失败不得返回 `ABNORMAL` saved state
- rebuild 失败后不得继续主循环
- cleanup -> wait no open orders -> rebuild 的顺序不可改变

### Placement Semantics

rebuild placement 的目标是放出：

- 一组合法 `PAIR`
- 或余额不足下可接受的合法单边 residual

当一侧余额不足时：

- 仅允许按实现显式允许的方向退化为单边 residual
- 不允许构造未定义 state shape
- 不允许沿用旧 state 假装成功

## 14. Price, Sizing, and Exception Contract

### Price Comparison

engine / strategy / execution 内部关键价格判定必须基于离散化比较。

至少以下逻辑必须离散化：

- price equality
- pair gap match
- threshold distance check
- normalize 后的 canonical price comparison

不允许：

- 关键 pair 判定直接使用 float equality
- gap 判定直接依赖未离散化浮点减法
- anchor break 直接依赖原始浮点距离

### Sizing Invariant

当前 sizing invariant 为：

- **same notional**
- 不是 same base size

即：

- buy / sell 两边允许有不同 BTC size
- 只要两边各自按相同 `BUDGET_USDC` 推导，就视为合法
- size 不参与 pair validity 判定

### Exception Handling

运行期异常必须统一收口为：

- 记录 stage
- 记录异常摘要
- 输出 `abnormal`
- 停止运行

当前统一收口阶段至少包括：

- startup
- bootstrap
- main-loop

约束：

- engine 不允许吞掉未知异常后继续运行
- gateway / execution 抛出的异常必须由 engine shell 统一收口
- failure 后不得继续使用旧 saved state 运行

## 15. Invariants

系统必须满足：

- saved state 永远不为 `ABNORMAL`
- engine 持有唯一 saved state
- 每轮只产生一个 action
- decision 不做 IO
- gateway 不做状态机
- execution 不做策略决策
- 主循环固定为 REST polling
- `pair_center_price` 与 `reference_price` 不可混用
- fill-driven rebuild 优先级最高
- `PAIR` keep 必须同时比较 buy / sell 两侧
- `BUY_ONLY` 不得依赖 `sell_price`
- `SELL_ONLY` 不得依赖 `buy_price`
- rebuild 失败后不得继续主循环
- strategy 不得自行扩展 accepted path
- implementation 不得在合同外引入隐式状态语义