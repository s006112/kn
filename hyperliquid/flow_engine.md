# flow_engine.md

本文档只描述当前 grid engine shell 的流程顺序，不描述 single-pair strategy 的内部细节。

它依赖：
- `contract_grid_engine.md`
- `contract_strategy_pair.md`

## 1. Purpose

当前 engine shell 的职责是：

- 启动系统并加载运行依赖
- 获取当前 open orders snapshot
- 把 snapshot 交给 strategy decision layer
- 根据 strategy 返回结果执行 keep / rebuild / abnormal
- 只在成功得到非 `ABNORMAL` 新状态后继续主循环

本文档不定义 pair strategy 的内部判定细节，也不定义未来 rolling ladder strategy。

## 2. Engine Shell Flow

### 2.1 Startup Flow

1. 启动 engine
2. 加载环境变量与常量
3. 校验 `ACCOUNT_ADDRESS`
4. 创建 `Info` client
5. 创建 `Exchange` client
6. 拉取当前 `open orders`
7. 记录当前订单摘要
8. 尝试接受初始 saved state，或进入初始 rebuild

### 2.2 Initial State Acceptance Flow

当前 startup 只接受以下两类起点：

#### A. 接受已有 `PAIR` snapshot

只有同时满足以下条件时，engine 才直接进入主循环：

- 当前 open-order shape 可被分类为 `PAIR`
- `get_pair_state(...)` 成功
- 该 pair snapshot 被接受为初始 saved state

若成立：
- 初始 saved state = 当前 pair snapshot
- 进入主循环

#### B. 初始 rebuild

若当前 snapshot 不能被接受为初始 `PAIR` state，则：

1. 进入 `rebuild(...)`
2. 若当前存在 live orders，则先尝试清理
3. 若清理失败，则直接退出
4. 若调用方提供显式 `reference_price`，则沿用该值
5. 否则调用 `get_mid_reference_price()` 推导 fresh reference
6. 调用 `place_pair(...)`
7. 若返回 `PAIR` / `BUY_ONLY` / `SELL_ONLY`，则接受为新 saved state 并进入主循环
8. 若返回 `ABNORMAL` 或 rebuild failure，则退出

## 3. Main Loop Shell Flow

每轮循环固定按以下顺序运行：

1. sleep
2. 拉取当前 `open orders`
3. 记录当前订单摘要
4. 调用 strategy decision layer
5. 读取 strategy 返回动作：
   - `("keep", None)`
   - `("rebuild", reference_price)`
   - `("rebuild", None)`
   - `("abnormal", None)`

### 3.1 Keep Path

若 strategy 返回 `("keep", None)`：

- engine 不做下单或撤单
- 直接进入下一轮循环

### 3.2 Rebuild Path

若 strategy 返回任意 `("rebuild", ...)`：

1. 调用 `rebuild(...)`
2. 若当前存在 live orders，则先尝试清理
3. 若清理失败，则退出
4. 若传入显式 `reference_price`，则沿用该值
5. 若传入 `None`，则调用 `get_mid_reference_price()` 推导 fresh reference
6. 调用 `place_pair(...)`
7. 若返回 `PAIR` / `BUY_ONLY` / `SELL_ONLY`，则接受为新 saved state，并进入下一轮循环
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
- strategy 不得跳过 engine rebuild pipeline
- strategy 不得直接修改 live orders
- strategy 不得绕过 abnormal exit contract

当前 engine 不关心 strategy 内部到底是：
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
- future rolling ladder / moving window strategy