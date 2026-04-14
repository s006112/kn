# Grid Engine Contract

## 1. Purpose

本合同定义当前 grid engine 的唯一行为边界。

engine 的职责是：

- 维护 saved state
- 维护供决策层消费的当前 live input
- 在 `keep`、`rebuild`、`abnormal` 之间做唯一决策
- 仅在得到非 `ABNORMAL` 新状态后继续运行
- 对运行期异常做统一收口并 abnormal exit

engine 是 transport-agnostic 的：
策略语义不得因 REST、polling、WebSocket 或其他输入机制而改变。

## 2. Accepted State Model

saved state 只接受：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`

`ABNORMAL` 只允许作为失败结果或退出结果，不允许作为继续运行的 saved state。

允许两种 state schema：

### Pair-derived state

- `mode`
- `buy_price`
- `sell_price`
- `pair_center_price`

### Placement / rebuild state

- `mode`
- `buy_price`
- `sell_price`
- `reference_price`

约束：

- `pair_center_price` 表示当前 live pair 中点
- `reference_price` 表示 placement / rebuild anchor
- 两者不可混用，不可互相替代

## 3. Live Input Boundary

engine 必须基于当前 live input 做决策。

live input 至少包括：

- 当前 saved state
- 当前 live orders
- 当前按需读取的 BTC mid
- rebuild in-flight guard

约束：

- decision 只依赖当前输入，不依赖历史事件序列
- decision 层不得直接读取 raw transport event
- input mechanism 可以是 snapshot read，也可以是事件驱动更新后的内存态

## 4. Gateway Boundary

`grid_gateway.py` 是 engine 与外部系统的唯一读取边界。

职责：

- 读取 open orders 与 mid price
- 标准化订单 shape
- 统一 price normalization
- 对短暂性失败做有限重试

约束：

- decision 层不得直接依赖底层 client
- gateway 可以抛出异常
- engine shell 必须统一收口，不得在未知输入上继续运行

## 5. Price Comparison Contract

engine 内部关键价格判定必须基于离散化比较，而不是直接依赖 float 精确相等。

至少以下逻辑必须离散化：

- price equality
- pair gap match
- threshold distance check

不允许：

- 关键 pair 判定直接使用 float equality
- gap 判定直接依赖未离散化的浮点减法

## 6. Decision Contract

每轮 decision 只允许返回：

- `("keep", None)`
- `("rebuild", reference_price)`
- `("rebuild", None)`
- `("abnormal", None)`

约束：

- 同一输入必须产生同一输出
- 每轮只允许一个 action
- decision 顺序固定为：

1. classify open-order shape
2. fill-driven rebuild
3. `PAIR` keep
4. single-sided branch
5. abnormal

strategy-specific 语义由 `contract_strategy_pair.md` 定义。

## 7. Execution and Exception Contract

当前实现允许使用 polling loop 刷新 live input；这属于实现形式，不改变本合同定义的 decision 语义。

### Startup / Bootstrap

engine 启动时必须：

- 初始化 client
- 获取初始 live orders
- 接受合法初始 state，或执行 rebuild

若无法得到有效非 `ABNORMAL` state，则必须退出。

### Keep

当 decision 返回 `("keep", None)` 时：

- 不改变 saved state
- 不执行任何 order operation

### Rebuild

当 decision 返回 `("rebuild", reference_price)` 或 `("rebuild", None)` 时：

- 先 cleanup 旧挂单（如有）
- 构造并提交新订单
- 仅在得到 `PAIR / BUY_ONLY / SELL_ONLY` 时更新 saved state

若 rebuild 失败，必须 abnormal exit。

### Abnormal

当 decision 返回 `("abnormal", None)` 时：

- 记录异常摘要
- 立即退出
- 不做自动修复

### Unified Exception Handling

运行期异常必须统一收口为：

- 记录 stage
- 记录异常摘要
- 输出 `abnormal exit`
- 停止运行

## 8. Invariants

engine 必须满足：

- saved state 永远不为 `ABNORMAL`
- `pair_center_price` 与 `reference_price` 不可混用
- 每轮只产生一个 action
- 不允许并发 rebuild
- rebuild 必须是单一 in-flight transition
- rebuild 失败后不得继续主循环

当前 sizing invariant 明确为：

- **same notional**
- 不是 same base size

即：

- buy / sell 两边可有不同 BTC size
- 只要两边各自按相同 `BUDGET_USDC` 推导，就视为合法

## 9. Scope Boundary

本合同不定义：

- strategy 内部单边逻辑
- anchor break 细节
- 多层 grid / ladder / rolling strategy
- transport 层具体消息结构

以上内容由其他 contract 或实现定义。