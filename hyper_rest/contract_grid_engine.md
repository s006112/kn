# Grid Engine Contract

### Document Status

本文档定义当前 `hyper_rest` grid engine 的保守合同边界。当前代码实现是 REST 轮询驱动：engine 以固定 cadence 拉取当前 `open orders` 快照，并基于该快照与已接受的 saved state 做唯一决策。策略语义、决策顺序与交易规则以当前代码为准。

本文档只描述以 `hyper_rest/grid.py`、`hyper_rest/grid_infra.py`、`hyper_rest/grid_execution.py`、`hyper_rest/grid_decision.py` 与 `hyper_rest/grid_logic.py` 为边界的当前 grid engine contract。

## 1. Purpose

当前实现中，engine 的职责是：

- 维护一个已接受的运行态，并在每次轮询时读取当前 `open orders` 快照
- 基于 saved state 与当前快照，在 `keep`、fill-driven `rebuild`、`abnormal exit` 三类结果之间做唯一决策
- 只在成功得到非 `ABNORMAL` 新状态后继续运行

当前实现采用 REST snapshot 驱动的 engine 输入边界：

- `grid.py` 主循环先 `sleep`，再通过 `get_open_orders(...)` 读取最新快照
- 决策层消费的是当前轮询得到的 `open orders` 快照与 saved state
- fill-driven `rebuild` 由“saved state 与当前快照的关系”推导，而不是由事件类型直接触发
- 当前实现允许在需要时额外读取 BTC mid 等基础设施输入，但它们只作为单边重锚或重建参考价的辅助输入
- 策略语义独立于 transport 身份；当前合同只描述已经实现的轮询边界与决策边界

策略特定的单边 residual stale 处理不属于本文档，见 `contract_strategy_pair.md` 中的 `Anchor Break Subcontract`。
- residual stale = “一个单边残单，而且它已经不再符合当前 grid / anchor / contract，因此必须触发 rebuild”

## 2. Accepted State Model

engine 的 saved state 只接受以下 `mode`：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`

`ABNORMAL` 只作为判定结果或失败结果存在，不作为下一次继续运行的有效 saved state。

当前实现承认两种状态 schema，且不会把它们视为同一字段语义：

- pair-derived state: `mode`、`buy_price`、`sell_price`、`pair_center_price`
- placement / rebuild state: `mode`、`buy_price`、`sell_price`、`reference_price`

其中：

- `pair_center_price` 只是从当前有效 pair 推导出的中点
- `reference_price` 是下单或 rebuild 使用的锚点
- engine 不会在决策链中把两者合并或互相替代

## 3. Live-State And Input Model

本 engine contract 只要求以下当前输入边界：

- engine 维护上一轮已接受的 saved state
- engine 在每轮主循环中读取当前 `open orders` 快照，并把它作为该轮判定的当前订单事实来源
- 决策层读取的是当前轮询快照与按需读取的当前输入值，而不是任何未建模的外部事件流
- 每次触发决策前，必须先得到本轮最新的 `open orders` 快照

本合同不要求：

- 维护独立的 WebSocket 事件入口
- 把 transport 订阅格式、重连策略或可靠性承诺写入策略语义
- 在 saved state 之外再维护一套事件驱动的额外状态机

## 4. Open-Order Shape Model

当前 live orders 形态只按以下规则分类：

- `PAIR`: 恰好 `1 BUY + 1 SELL`，且该 pair 严格满足理论价差 `sell_price - buy_price == (buy_grid_factor + sell_grid_factor) * grid_step`
- `BUY_ONLY`: 恰好 `1 BUY + 0 SELL`
- `SELL_ONLY`: 恰好 `0 BUY + 1 SELL`
- `ABNORMAL`: 其他所有情况

当前实现故意采用严格不变式边界：

- 只接受严格满足理论价差的 pair
- 不接受近似匹配或接近理论价差的 pair
- 不接受任何“差一点”的 live pair

`ABNORMAL` 包括但不限于：

- 无效的 `1 BUY + 1 SELL`
- fill contract 之外的 `0 orders`
- 任意多余残单

## 5. Main Decision Order

每次决策严格按以下顺序判定：

1. 对当前 live orders 做 `classify_order_shape(...)`
2. 优先检查 fill-driven rebuild
3. 再检查 `PAIR` 是否满足 keep
4. 再检查单边状态的专属处理分支
5. 以上都不满足则返回 `abnormal`

其中第 4 步的单边专属处理由子合同定义；但它永远不能早于 shape 校验，也永远不能覆盖 fill-driven rebuild。

## 6. Fill-Driven Rebuild

fill-driven rebuild 的优先级高于任何 keep 分支和任何单边专属分支。

当前实现中没有独立的 fill 事件入口。是否命中该路径，只由 saved state 与当前轮询到的 `open orders` 快照之间的合同关系决定。

### 6.1 Saved `PAIR`

当 saved `mode == PAIR` 时：

- 当前形态为 `SELL_ONLY`，表示之前的 buy 已成交
- 当前形态为 `BUY_ONLY`，表示之前的 sell 已成交

此时必须进入 `("rebuild", reference_price)`，并使用“已成交那一侧”的保存价格作为 `reference_price`。

这里不是 fresh mid-grid rebuild。

### 6.2 Saved `BUY_ONLY`

当 saved `mode == BUY_ONLY` 且当前 `open orders` 数量为 `0` 时：

- 视为合法 residual completion
- 返回 `("rebuild", state["buy_price"])`

### 6.3 Saved `SELL_ONLY`

当 saved `mode == SELL_ONLY` 且当前 `open orders` 数量为 `0` 时：

- 视为合法 residual completion
- 返回 `("rebuild", state["sell_price"])`

只要命中以上路径，就不能把该 completion 当作 `abnormal`。

## 7. Keep Contract

### 7.1 `PAIR` keep

只有同时满足以下条件时，`PAIR` 才能继续 `keep`：

- 当前分类结果仍然是 `PAIR`
- `get_pair_state(...)` 成功
- 当前 `buy_price` 严格等于 saved `buy_price`
- 当前 `sell_price` 严格等于 saved `sell_price`

否则 `PAIR` 不得 `keep`。

### 7.2 Single-Sided Keep

`BUY_ONLY` / `SELL_ONLY` 是否允许继续 `keep`，由策略子合同定义；engine 只保证：

- 该判定发生在 fill-driven rebuild 之后
- 该判定发生在 `PAIR` keep 之后
- 若单边专属分支未接受当前状态，则结果落入 `abnormal`

## 8. Rebuild Success And Failure

`rebuild(...)` 只有在 `place_pair(...)` 返回“非 `ABNORMAL` mode”时才算成功。

成功结果只有三种：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`

其他所有情况都视为 rebuild failure，包括：

- `place_pair(...)` 返回 `ABNORMAL`
- rebuild 前清理订单失败
- 下单失败后的二次清理失败

一旦 rebuild failure，engine 立即退出。

rebuild 在合同上视为单个 in-flight transition。engine 不得执行重叠的 rebuild；重复或重复观察到的轮询快照也不得导致重复 rebuild 动作。

## 9. Abnormal / Exit Contract

当前合同是故意收窄的。凡是不属于已接受分支的情况，都会落入 `abnormal` 并退出。

例如：

- saved `PAIR`，但当前形态是无效 pair 或多余残单
- saved `BUY_ONLY`，但当前形态变成 `SELL_ONLY`
- saved `SELL_ONLY`，但当前形态变成 `BUY_ONLY`
- saved 单边模式，但当前 live orders 数量超过 `1`
- 当前 pair 不再严格满足理论价差
- 当前 pair 的买卖价格与 saved pair 不再完全一致
- fill / keep / 单边专属分支检查后，仍未命中已接受分支的其他形态

当前实现不会尝试对这些情况做部分修复。

## 10. Logging Contract

当前实现的 engine 日志约定包括：

- keep 类日志
- fill completion 日志
- abnormal summary 与 abnormal exit 日志
- rebuild failure / abnormal rebuild exit 日志

其中：

- keep 类日志走 rate limit
- 非 keep 退出类日志不走 keep rate limit

具体的单边专属分支日志由策略子合同定义。

## 11. Out Of Scope

本文档不定义：

- WebSocket transport 的具体订阅、重连、去重或可靠性实现
- 当前实现之外的未来事件驱动同步机制
- anchor break 的触发条件、动作语义和非目标
- 任意 abnormal 订单形态的通用恢复机制
- 当前实现之外的未来扩展策略

## Repository Constraints

- 该约束适用于当前 grid engine 项目目录及其子目录（recursive），不包括外部依赖或第三方库。
- 以下约束不改变 strategy semantics、decision logic 或 execution behavior。
- 以下约束只服务于 maintainability、AI-tool compatibility 与 structural simplicity。

### File Count Constraint

- 项目总文件数 MUST NOT 超过 `10`。
- 统计范围包括当前项目目录及其子目录中的所有 Python code files、contract documents、flow documents 与 service files。
- 明确排除：外部依赖、第三方库、虚拟环境目录（如 `.venv`）、缓存目录（如 `__pycache__`）、版本控制目录（如 `.git`）。
- 这是 hard constraint，不是 guideline。
- 任何 refactor 都 MUST 保持该上限。
- 新增文件前，必须先合并或移除现有文件。
- 不允许通过子目录拆分来规避文件数量限制。
- 该约束不得作为破坏当前 clean module boundaries 的理由。

### Allowed Format Constraint

- YAML format NOT allowed。
- 不允许任何 `.yaml` 或 `.yml` 文件。
- 不允许 YAML-based configuration。
- 不允许 YAML-based schema 或 contract definition。
- 允许的格式仅限 `.py`、`.md` 与 minimal `.service` 或等价系统服务定义文件。
