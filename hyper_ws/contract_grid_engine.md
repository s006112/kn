# Grid Engine Contract
## Document Status
本文档定义 grid engine 的 WebSocket-first migration baseline。它描述目标 engine worldview，未必与当前生产代码实现完全一致；当前运行时仍可能保留 REST 轮询边界。
本合同为 engine 行为的唯一定义来源。策略语义、决策顺序与交易规则保持不变，strategy 继续消费 engine 提供的 live state，而不是依赖 transport 形式。
本合同覆盖以下模块边界：
* `grid.py`
* `grid_gateway.py`
* `grid_execution.py`
* `grid_decision.py`

## 1. Purpose
engine 的职责是：
* 维护一个已接受的运行态（saved state）
* 维护一个供决策层消费的当前 live state
* 在 `keep`、fill-driven `rebuild`、`abnormal exit` 三类结果之间做唯一决策
* 只在成功得到非 `ABNORMAL` 新状态后继续运行
engine 是 transport-agnostic 的：
* 策略语义独立于 input mechanism
* engine 不依赖 WebSocket 或 REST 的具体实现形式

## 2. Accepted State Model
engine 的 saved state 只接受：
* `PAIR`
* `BUY_ONLY`
* `SELL_ONLY`
`ABNORMAL`：
* 仅作为判定结果或失败结果
* 不允许作为下一轮运行的有效 state
本合同承认两种状态 schema：
### Pair-derived state
* `mode`
* `buy_price`
* `sell_price`
* `pair_center_price`
### Placement / rebuild state
* `mode`
* `buy_price`
* `sell_price`
* `reference_price`
约束：
* `pair_center_price` 仅为当前 pair 的中点
* `reference_price` 为下单或 rebuild 的锚点
* 两者不可互相替代，不可混用

## 3. Live-State and Input Model
engine 必须维护一个 authoritative in-memory live state：
* 所有决策必须基于该 live state
* 决策层不得直接读取 raw events 或 transport 数据
输入模型要求：
* engine 消费“当前 live orders + 当前值（如 mid price）”
* 输入来源可以是 WebSocket 或 snapshot read
* 输入机制不影响策略语义
事件优先级：
* fill event 是最高价值触发源
* order update 仅更新 state，不改变策略语义

## 4. Gateway Boundary
`grid_gateway.py` 是 engine 与外部系统的唯一读取边界：
职责：
* 提供 open orders、mid price 等读取接口
* 吸收短暂性 API / 网络失败
* 提供有限重试与稳定输出
约束：
* decision 层不得直接依赖底层 client
* flow 不包含任何 transport 实现细节
* snapshot read 属于 gateway implementation detail

## 5. Engine Decision Contract
engine 每次决策必须返回唯一结果：
* `("keep", None)`
* `("rebuild", reference_price)`
* `("rebuild", None)`
* `("abnormal", None)`
决策必须满足：
* deterministic（同一输入 → 同一输出）
* 不依赖历史事件序列，仅依赖当前 state
决策顺序：
1. classify open-order shape
2. 优先处理 fill-driven rebuild
3. 再处理 `PAIR` keep
4. 再处理单边 residual 分支
5. 否则为 `abnormal`
strategy-specific 规则见 `contract_strategy_pair.md`

## 6. Engine Execution Semantics
### 6.1 Startup
engine MUST：
* 初始化运行依赖与 client
* 建立初始 live state
* 尝试接受初始 saved state 或执行初始 rebuild
engine ONLY 在以下条件满足时进入运行态：
* 已获得非 `ABNORMAL` 的有效 state

### 6.2 Main Loop Semantics
engine 行为定义为：
```text
event / input
    → update live state
    → decision
    → execution
```
约束：
* event intake 是逻辑上的主驱动
* 固定 sleep loop 不是 contract 的组成部分
* snapshot polling 不是定义性步骤

### 6.3 Keep
当 decision 返回：
```text
("keep", None)
```
engine MUST：
* 不改变 state
* 不执行任何 order 操作
* 继续下一轮输入处理

### 6.4 Rebuild
当 decision 返回：
```text
("rebuild", reference_price)
或 ("rebuild", None)
```
engine MUST：
* 执行 cleanup（若需要）
* 基于 reference_price 或策略规则构建新订单
* 更新 saved state
成功条件：
* 新 state 为 `PAIR` / `BUY_ONLY` / `SELL_ONLY`
失败条件：
* 返回 `ABNORMAL`
* 或无法建立有效 state

### 6.5 Abnormal
当 decision 返回：
```text
("abnormal", None)
```
engine MUST：
* 立即退出运行
* 记录异常摘要
* 不尝试自动修复

## 7. Invariants
engine 必须满足：
### State Invariants
* saved state 永远不为 `ABNORMAL`
* state schema 不混用
### Decision Invariants
* 每轮只产生一个 action
* decision 仅依赖当前输入
### Execution Invariants
* 不允许重复 rebuild
* 不允许并发 rebuild
* rebuild 必须是单一 in-flight transition

## 8. Abnormal / Exit Contract
以下情况必须退出：
* rebuild 失败
* rebuild 返回 `ABNORMAL`
* decision 返回 `abnormal`
* 无法建立有效 state
exit 后：
* engine 不再继续运行
* 不进入自动恢复流程

## 9. Repository Constraints
### File Count
* 项目总文件数 MUST NOT 超过 10
### Allowed Format
* 仅允许 `.py`、`.md`、`.service`
* 禁止 `.yaml` / `.yml`

## 10. Scope Boundary
本合同不定义：
* strategy 内部逻辑
* anchor break 细节
* 多层 grid / ladder
相关内容见：
* `contract_strategy_pair.md`
