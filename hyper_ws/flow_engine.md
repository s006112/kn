# flow_engine.md

### Document Status

本文档定义 grid engine shell 的 WebSocket-first migration baseline flow。它描述目标 engine worldview，未必与当前生产代码实现或当前运行时行为完全一致；当前生产运行时仍可能保留 REST 轮询边界。策略语义、决策顺序与交易规则保持不变，strategy 继续消费 engine 提供的 live state，而不是依赖 transport 形式。

本文档只描述 grid engine shell migration baseline 的流程顺序，不描述 single-pair strategy 的内部细节。

它依赖：
- `contract_grid_engine.md`
- `contract_strategy_pair.md`

## 1. Purpose

本 migration baseline 中，engine shell 的职责是：

- 启动系统并加载运行依赖
- 建立初始 accepted state 与初始 live state
- 启动 WebSocket / user-event intake
- 在每次事件更新后把当前 live state 交给 strategy decision layer
- 根据 strategy 返回结果执行 keep / rebuild / abnormal
- 只在成功得到非 `ABNORMAL` 新状态后继续运行

其中外部交易所读取边界由 `grid_gateway.py` 承担：

- shell / execution 通过 gateway 读取 open orders 与 mid reference 所需数据
- gateway 负责吸收短暂性 API / 网络失败的有限重试
- flow 只依赖 gateway 提供的读取结果，不把底层 transport client 细节写进主流程

本文档采用 WebSocket-first 的 shell flow：

- event intake 是 engine 的顶层驱动
- “收到事件 -> 更新状态 -> 调用决策 -> 执行动作” 是主流程原语
- 固定 sleep loop 不是当前 flow contract 的组成部分
- REST `open orders` polling 不是当前 flow contract 的定义性主步骤

若存在启动阶段或 rebuild 阶段的 snapshot read，它们通过 `grid_gateway.py` 进入 shell flow，属于 gateway implementation detail，而不是 flow contract 的顶层驱动原语。

本文档不定义 pair strategy 的内部判定细节，也不定义未来 rolling ladder strategy。

## 2. Engine Shell Flow

### 2.1 Startup Flow

1. 启动 engine
2. 加载环境变量与常量
3. 校验 `ACCOUNT_ADDRESS`
4. 创建运行时依赖与 client
5. 建立初始 live state
6. 记录初始订单摘要
7. 尝试接受初始 saved state，或进入初始 rebuild
8. 启动 WebSocket / user-event intake
9. 进入事件驱动运行阶段

### 2.2 Initial State Acceptance Flow

本 migration baseline 的 startup 只接受以下两类起点：

#### A. 接受已有 `PAIR` live state

只有同时满足以下条件时，engine 才直接进入事件驱动运行阶段：

- 当前 live-order shape 可被分类为 `PAIR`
- `get_pair_state(...)` 成功
- 该 pair state 被接受为初始 saved state

若成立：
- 初始 saved state = 当前 pair state
- 进入事件驱动运行阶段

#### B. 初始 rebuild

若当前 live state 不能被接受为初始 `PAIR` state，则：

1. 进入 `rebuild(...)`
2. 若当前存在 live orders，则先尝试清理
3. 若清理失败，则直接退出
4. 若调用方提供显式 `reference_price`，则沿用该值
5. 否则调用 `get_mid_reference_price()` 推导 fresh reference
6. 调用 `place_pair(...)`
7. 若返回 `PAIR` / `BUY_ONLY` / `SELL_ONLY`，则接受为新 saved state，并进入事件驱动运行阶段
8. 若返回 `ABNORMAL` 或 rebuild failure，则退出

## 3. Event-Driven Shell Flow

事件驱动运行阶段固定按以下顺序处理：

1. 等待 user event
2. 收到 event
3. 用该 event 更新当前 live state
4. 把当前 saved state 与更新后的 live state 交给 strategy decision layer
5. 读取 strategy 返回动作：
   - `("keep", None)`
   - `("rebuild", reference_price)`
   - `("rebuild", None)`
   - `("abnormal", None)`
6. 按返回动作执行 keep / rebuild / abnormal

其中：

- engine 维护 authoritative in-memory live state；该 state 是决策层的单一事实来源，决策不得直接读取 raw events
- fill / order-update 风格事件都可以推动 state 更新并进入后续判定
- fill event 是 rebuild path 的主要触发来源；其他 order-update 事件可以更新 state，但不会由事件类型本身重定义 rebuild 语义
- 本 flow 只要求“先更新 state，再基于更新后的 live state 做决策”，不规定具体 WebSocket library 细节

### 3.1 Keep Path

若 strategy 返回 `("keep", None)`：

- engine 不做下单或撤单
- 继续等待下一次 event

### 3.2 Rebuild Path

若 strategy 返回任意 `("rebuild", ...)`：

1. 调用 `rebuild(...)`
2. 若当前存在 live orders，则先尝试清理
3. 若清理失败，则退出
4. 若传入显式 `reference_price`，则沿用该值
5. 若传入 `None`，则调用 `get_mid_reference_price()` 推导 fresh reference
6. 调用 `place_pair(...)`
7. 若返回 `PAIR` / `BUY_ONLY` / `SELL_ONLY`，则接受为新 saved state，并继续等待下一次 event
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
- strategy 消费的是 engine 提供的当前 live state
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
- WebSocket transport 的具体实现细节
- future rolling ladder / moving window strategy
