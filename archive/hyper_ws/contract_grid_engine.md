# Grid Engine Contract

## 1. Purpose

本合同定义当前 grid engine 的唯一运行边界。

engine 的职责是：

- 持有当前 saved state
- 以固定 polling loop 运行
- 通过 REST 读取当前 live orders
- 仅在 `BUY_ONLY` 时按需读取 BTC mid
- 基于当前输入在 `keep`、`rebuild`、`abnormal` 之间做唯一决策
- 仅在得到有效新 state 后继续运行
- 对运行期异常做统一收口并退出

当前 engine 是纯 REST / polling 实现。
策略语义不得因 polling 读取实现而改变。

## 2. Accepted State Model

saved state 只接受：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`

`ABNORMAL` 不允许作为继续运行的 saved state。
abnormal 只允许作为失败结果或退出结果。

允许以下 state shape：

### Pair live state

用于 bootstrap 时从当前 live orders 直接识别合法 pair：

- `mode`
- `buy_price`
- `sell_price`
- `pair_center_price`

含义：

- 表示当前 live orders 已被识别为合法 `PAIR`
- `pair_center_price` 是当前 live pair 的 canonical center

### Pair rebuild state

用于 placement / rebuild 成功后返回的新状态：

- `mode`
- `buy_price`
- `sell_price`
- `reference_price`

含义：

- 表示本次 rebuild 成功放出一组合法 pair
- `reference_price` 是该次 rebuild 使用的 anchor

### Bootstrap residual state

仅适用于 engine startup 时直接接受合法单边 residual：

#### BUY_ONLY residual

- `mode`
- `buy_price`

#### SELL_ONLY residual

- `mode`
- `sell_price`

含义：

- 表示启动时 live orders 已经是合法单边 residual
- 只保存后续决策所需的 canonical price
- 不要求补齐另一侧 price
- 不要求构造 `pair_center_price`
- 不要求构造 `reference_price`

### Rebuild single-sided state

用于 rebuild 成功但另一侧因余额不足未成功挂出：

#### BUY_ONLY rebuild state

- `mode`
- `buy_price`
- `reference_price`

#### SELL_ONLY rebuild state

- `mode`
- `sell_price`
- `reference_price`

含义：

- 表示本次 rebuild 成功放出合法单边 residual
- `reference_price` 是该次 rebuild 使用的 anchor

约束：

- `pair_center_price` 表示 live pair center
- `reference_price` 表示 rebuild anchor
- 两者不可混用，不可互相替代
- `BUY_ONLY` state 不得依赖 `sell_price`
- `SELL_ONLY` state 不得依赖 `buy_price`

## 3. Live Input Boundary

每轮 decision 只能基于当前输入。

当前 live input 只包括：

- 当前 `saved_state`
- 当前 `live orders`
- 当前按需读取的 `BTC mid`

约束：

- decision 只依赖当前输入
- decision 不依赖历史事件序列
- decision 层不得直接做 IO 读取
- engine 不维护事件驱动内存态
- engine 不维护 runtime input 容器
- engine 不维护额外的 rebuild 运行态标志

## 4. Gateway Boundary

`grid_gateway.py` 是 engine 的唯一读取边界。

职责：

- 通过 REST 读取 open orders
- 通过 REST 读取 BTC mids
- 归一化 order shape
- 统一 price normalization
- 对短暂性 infra read failure 做有限重试

约束：

- decision 层不得直接依赖底层 client
- retry 只允许存在于读取侧
- retry 不得扩散到 decision 层
- gateway 可以抛出异常
- engine 必须统一收口，不得在未知输入上继续运行

## 5. Price Comparison Contract

engine 内部关键价格判定必须基于离散化比较，而不是直接依赖 float 精确相等。

至少以下逻辑必须离散化：

- price equality
- pair gap match
- threshold distance check
- normalize 后的 canonical price comparison

不允许：

- 关键 pair 判定直接使用 float equality
- gap 判定直接依赖未离散化的浮点减法
- anchor break 直接依赖原始浮点距离

## 6. Bootstrap Contract

engine 启动时必须：

1. 初始化 client
2. 读取当前 open orders
3. 输出当前 open orders 摘要
4. 尝试从 live orders 识别合法 bootstrap state
5. 若识别失败，则执行 rebuild

bootstrap 只允许接受：

- 合法 `PAIR`
- 合法 `BUY_ONLY`
- 合法 `SELL_ONLY`

若无法得到有效 state，则必须退出。

约束：

- bootstrap 不允许继续持有无效 state
- bootstrap rebuild 失败必须退出
- bootstrap 时接受单边 residual 是合法路径，不属于 abnormal

## 7. Decision Contract

每轮 decision 只允许返回：

- `("keep", None)`
- `("rebuild", reference_price)`
- `("rebuild", None)`
- `("abnormal", None)`

约束：

- 同一输入必须产生同一输出
- 每轮只允许一个 action
- decision 顺序固定为：

1. classify current order shape
2. fill-driven rebuild
3. `PAIR` keep
4. single-sided branch
5. abnormal

strategy-specific 语义由 `contract_strategy_pair.md` 定义。

## 8. Main Loop Contract

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

## 9. Execution Contract

`grid_execution.py` 的职责是：

- cleanup 现有挂单
- wait until no open orders
- 构造 pair order actions
- 提交 buy / sell limit orders
- 根据下单结果分类返回新 state 或失败

### Cleanup

cleanup 语义必须为：

- 若无 open orders，则直接成功
- 若有 open orders，则先 bulk cancel
- 之后轮询确认 no open orders
- 若确认失败，则 rebuild 失败

### Placement Result Classification

下单结果只允许分为：

- `ok`
- `insufficient_spot_balance`
- `error`

rebuild 后新状态只允许是：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`
- `None`（表示失败）

约束：

- rebuild 失败不得返回 `ABNORMAL` saved state
- rebuild 失败后不得继续主循环
- cleanup -> wait no open orders -> rebuild 的顺序不可改变

## 10. Exception Handling Contract

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

## 11. Invariants

engine 必须满足：

- saved state 永远不为 `ABNORMAL`
- 每轮只产生一个 action
- decision 不做 IO
- gateway 不做状态机
- execution 不做策略决策
- 主循环固定为 REST polling
- `pair_center_price` 与 `reference_price` 不可混用
- rebuild 失败后不得继续主循环

当前 sizing invariant 明确为：

- **same notional**
- 不是 same base size

即：

- buy / sell 两边允许有不同 BTC size
- 只要两边各自按相同 `BUDGET_USDC` 推导，就视为合法

## 12. Scope Boundary

本合同不定义：

- 更高层 grid / ladder / rolling strategy
- transport 抽象层
- 事件驱动状态同步
- 多组 pair
- 自动异常修复
- placement 后更复杂的补救策略
- strategy 的具体单边逻辑细节

以上内容由 `contract_strategy_pair.md` 或实现定义。