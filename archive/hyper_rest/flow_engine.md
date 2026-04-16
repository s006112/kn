# flow_engine.md

### Document Status

本文档定义当前 `hyper_rest` grid engine shell 的实际流程。当前代码实现不是 WebSocket shell，而是固定 cadence 的 REST 轮询循环：先读取当前 `open orders` 快照，再调用 strategy decision layer，并据此执行 `keep`、`rebuild` 或 `abnormal exit`。

本文档只描述当前 grid engine shell 的流程顺序，不描述 single-pair strategy 的内部细节。

它依赖：
- `contract_grid_engine.md`
- `contract_strategy_pair.md`

## 1. Purpose

当前实现中，engine shell 的职责是：

- 启动系统并加载运行依赖
- 建立初始 accepted state
- 在每轮轮询后把当前 `open orders` 快照交给 strategy decision layer
- 根据 strategy 返回结果执行 keep / rebuild / abnormal
- 只在成功得到非 `ABNORMAL` 新状态后继续运行

本文档采用当前 polling shell flow：

- 固定 sleep loop 是 engine 的顶层驱动
- “sleep -> 读取 `open orders` -> 调用决策 -> 执行动作” 是主流程原语
- REST `open orders` polling 是当前 flow contract 的定义性主步骤

本文档不定义 pair strategy 的内部判定细节，也不定义未来 rolling ladder strategy。

## 2. Engine Shell Flow

### 2.1 Startup Flow

1. 启动 engine
2. 加载环境变量与常量
3. 校验 `ACCOUNT_ADDRESS`
4. 创建运行时依赖与 client
5. 读取初始 `open orders` 快照
6. 记录初始订单摘要
7. 尝试接受初始 saved state，或进入初始 rebuild
8. 进入轮询运行阶段

### 2.2 Initial State Acceptance Flow

当前实现的 startup 只接受以下两类起点：

#### A. 接受已有 `PAIR` 初始快照

只有同时满足以下条件时，engine 才直接进入轮询运行阶段：

- 当前 live-order shape 可被分类为 `PAIR`
- `get_pair_state(...)` 成功
- 该 pair state 被接受为初始 saved state

若成立：
- 初始 saved state = 当前 pair state
- 进入轮询运行阶段

#### B. 初始 rebuild

若当前初始 `open orders` 快照不能被接受为初始 `PAIR` state，则：

1. 进入 `rebuild(...)`
2. 若当前存在 live orders，则先尝试清理
3. 若清理失败，则直接退出
4. 若调用方提供显式 `reference_price`，则沿用该值
5. 否则调用 `get_mid_reference_price()` 推导 fresh reference
6. 调用 `place_pair(...)`
7. 若返回 `PAIR` / `BUY_ONLY` / `SELL_ONLY`，则接受为新 saved state，并进入轮询运行阶段
8. 若返回 `ABNORMAL` 或 rebuild failure，则退出

## 3. Polling Shell Flow

轮询运行阶段固定按以下顺序处理：

1. `sleep(MAIN_LOOP_POLL_INTERVAL_SEC)`
2. 读取当前 `open orders` 快照
3. 把当前 saved state 与该快照交给 strategy decision layer
4. 读取 strategy 返回动作：
   - `("keep", None)`
   - `("rebuild", reference_price)`
   - `("rebuild", None)`
   - `("abnormal", None)`
5. 按返回动作执行 keep / rebuild / abnormal

其中：

- engine 维护 authoritative saved state；当前 `open orders` 快照是该轮决策的当前订单事实来源
- fill-driven rebuild 由 saved state 与当前快照之间的关系推导，而不是由独立事件类型直接触发
- Anchor Break 或 fresh rebuild 可按需读取当前 BTC mid，但不改变主循环是轮询驱动这一事实

### 3.1 Keep Path

若 strategy 返回 `("keep", None)`：

- engine 不做下单或撤单
- 继续下一轮轮询

### 3.2 Rebuild Path

若 strategy 返回任意 `("rebuild", ...)`：

1. 调用 `rebuild(...)`
2. 若当前存在 live orders，则先尝试清理
3. 若清理失败，则退出
4. 若传入显式 `reference_price`，则沿用该值
5. 若传入 `None`，则调用 `get_mid_reference_price()` 推导 fresh reference
6. 调用 `place_pair(...)`
7. 若返回 `PAIR` / `BUY_ONLY` / `SELL_ONLY`，则接受为新 saved state，并继续下一轮轮询
8. 若返回 `ABNORMAL` 或 rebuild failure，则退出

### 3.3 Abnormal Path

若 strategy 返回 `("abnormal", None)`：

1. 记录 abnormal summary
2. 记录 abnormal exit
3. 退出

## 4. Rebuild Execution Boundary

engine shell 只负责 rebuild 的执行顺序：

- 清理旧订单
- 计算 reference
- 下新订单
- 接受新状态或退出

engine shell 不负责决定“为什么 rebuild”，该语义由 strategy contract 定义。

## 5. Strategy Boundary

engine shell 对 strategy layer 只要求：

- strategy 必须返回唯一动作结果
- strategy 消费的是 engine 提供的当前 `open orders` 快照与 saved state
- strategy 不得跳过 engine rebuild pipeline
- strategy 不得直接修改 live orders
- strategy 不得绕过 abnormal exit contract

在本 migration baseline 中，engine 不关心 strategy 内部到底是：
- fill-driven rebuild
- pair keep
- buy-only stale rebuild
- sell-only keep

这些都属于 strategy 自己的内部语义。

## 6. Out Of Scope

本文档不定义：

- `PAIR` keep 的内部条件
- `BUY_ONLY` / `SELL_ONLY` 分支条件
- anchor stale 触发条件
- 未来 WebSocket transport 的具体实现细节
- future rolling ladder / moving window strategy
