# flow_strategy_ladder.md

本文档只描述 future multi-grid / ladder strategy 的内部判定顺序，不描述 engine shell 的执行细节。

它依赖：
- `contract_strategy_ladder.md`
- `contract_grid_engine.md`

## 1. Purpose

当前 ladder strategy flow 的职责是：

- 读取 engine 传入的 saved ladder state 与当前 live orders
- 将当前 live orders 解释为 ladder contract 下的某种结构状态
- 以固定顺序返回唯一动作结果：
  - `("keep", None)`
  - `("rebuild", reference_price)`
  - `("rebuild", None)`
  - `("abnormal", None)`

本文档不定义 cleanup、place order、exchange client 或循环调度。

## 2. Ladder Strategy Decision Flow

当前 ladder strategy 的内部顺序必须严格固定为：

1. 读取当前 live orders snapshot
2. 将 live orders 解析为 current live ladder snapshot
3. 将 current live ladder snapshot 与 saved expected ladder 做比较
4. 优先检查 fill-driven ladder transition
5. 再检查 ladder keep
6. 再检查 ladder stale / drift rebuild
7. 以上都不满足则返回 `abnormal`

这一定序不能被打乱。

特别是：

- ladder stale / drift rebuild 不能早于 fill-driven transition
- ladder keep 不能覆盖 fill-driven transition
- abnormal 只能作为所有已接受分支都未命中后的兜底结果

## 3. Step 1: Read Live Orders Snapshot

strategy 首先读取 engine 当前传入的 `open orders` snapshot。

此时 strategy 不做下单，不做撤单，也不直接修改 live orders。

本步骤只负责获取本轮判定输入。

## 4. Step 2: Parse Live Ladder Snapshot

strategy 将当前 `open orders` 解析为 ladder contract 视角下的 current live ladder snapshot。

解析目标至少包括：

- 当前 live buy levels
- 当前 live sell levels
- 当前 live ladder 是否仍可映射到 saved ladder family
- 当前 live ladder 与 expected ladder 的关系类型

这里的 parse 结果只是后续判定输入，不直接决定最终动作。

## 5. Step 3: Compare Against Saved Expected Ladder

strategy 将 current live ladder snapshot 与 saved expected ladder 做比较。

比较的核心不是“当前还剩几张单”，而是：

- 当前 live levels 是否仍属于 saved ladder window
- 哪些 expected levels 缺失
- 是否出现 ladder contract 未定义的额外 levels
- 是否可以解释为合同允许的 ladder continuation
- 是否可以解释为 fill-driven ladder transition
- 是否已经 drift / stale

只有完成这一步，后续 keep / rebuild / abnormal 才有合同基础。

## 6. Step 4: Fill-Driven Ladder Transition

fill-driven ladder transition 拥有当前 ladder strategy 的最高优先级。

### 6.1 Trigger Meaning

当 current live ladder 相对于 saved expected ladder 出现 level 缺失，并且这种缺失可以被解释为合同允许的成交驱动变化时，进入 fill-driven ladder transition 分支。

这里的核心不是“单笔 fill”这个事实本身，而是：

- 缺失 level 是否与 expected ladder 一致
- 当前变化是否仍属于 ladder contract 允许的转移
- 该变化是否要求后续更新 anchor 或重建 ladder window

### 6.2 Action Meaning

只要命中 fill-driven ladder transition，strategy 就不得继续评估 keep，也不得直接落入 abnormal。

此时 strategy 只能返回：

- `("rebuild", reference_price)`，若合同要求沿用显式锚点
- `("rebuild", None)`，若合同要求交由 engine 使用 fresh reference

### 6.3 Net Shift Interpretation

在未来 ladder implementation 中，fill-driven transition 可以进一步解释为：

- missing sell levels count
- missing buy levels count
- net shift

也就是：

- `filled_sell_count = number of missing sell levels`
- `filled_buy_count = number of missing buy levels`
- `net_shift = filled_sell_count - filled_buy_count`

当 `net_shift != 0` 时，future ladder 可将其解释为 anchor window 的偏移量。

但在当前 flow 文档中：

- 允许这种解释方式
- 不强制具体实现公式
- 不强制此处必须做 partial repair

本步骤只定义：fill-driven transition 优先级最高，并且命中后必须进入 rebuild path。

## 7. Step 5: Ladder Keep

只有在未命中 fill-driven ladder transition 时，才允许评估 ladder keep。

### 7.1 Keep Meaning

ladder keep 表示：

- 当前 live ladder snapshot 仍可被解释为 saved expected ladder 的合法延续
- 当前偏差仍处于 ladder contract 允许范围内
- 当前状态不要求立即 rebuild
- 当前状态不属于 abnormal

### 7.2 Keep Result

若当前状态满足 ladder keep contract，则返回：

- `("keep", None)`

一旦返回 keep，本轮 strategy 不得继续评估 stale / drift rebuild，也不得返回 abnormal。

## 8. Step 6: Ladder Stale / Drift Rebuild

只有在未命中 fill-driven transition、且也未命中 ladder keep 时，才允许评估 stale / drift rebuild。

### 8.1 Stale / Drift Meaning

stale / drift rebuild 表示：

- 当前 live ladder 可能仍与 saved ladder 有部分关系
- 但这种关系已经不再满足 keep contract
- 如果继续 keep，会违反 ladder contract
- 因此必须 rebuild

### 8.2 Action Result

若命中 stale / drift rebuild，strategy 只能返回：

- `("rebuild", reference_price)`
- `("rebuild", None)`

具体沿用旧锚点还是交给 engine 使用 fresh reference，留给后续更细合同决定。

当前 flow 只规定：

- stale / drift rebuild 晚于 fill-driven transition
- stale / drift rebuild 晚于 keep
- stale / drift rebuild 早于 abnormal

## 9. Step 7: Abnormal

若前述所有已接受分支都未命中，则当前状态落入 abnormal。

也就是说，当 current live ladder snapshot：

- 不能被解释为 fill-driven ladder transition
- 不能被解释为 ladder keep
- 不能被解释为 stale / drift rebuild

则返回：

- `("abnormal", None)`

当前 ladder flow 采用严格边界：

- 不接受“看起来差不多”
- 不接受“局部合理但没有合同定义”
- 不接受“多出来一些 level 但先 keep 看看”

## 10. Engine Boundary

ladder strategy flow 只定义 strategy 内部判定顺序。

它不负责：

- cleanup
- place order
- rebuild execution
- abnormal exit

这些都继续由 engine shell 负责。

也就是说：

- ladder strategy 决定动作
- engine shell 执行动作

## 11. Relationship To Single-Pair Degeneration

当 `M = 1` 时，ladder 在对象模型上可退化为 single-pair structure。

因此，future ladder strategy flow 在更高层可以覆盖 single-pair family。

但这里必须区分两层意思：

- 对象模型上的退化成立
- 当前 pair contract 的行为语义是否 fully covered，需要额外满足更细的退化条件

因此，`M = 1` 只说明：

- ladder flow 可以拥有 single-pair 的上位解释能力

不自动表示：

- current pair fill / residual / anchor semantics 已被 fully absorbed

## 12. Future Implementation Notes

当前 flow 允许 future ladder implementation 使用以下解释骨架：

Initialization:
- place all expected sell levels
- place all expected buy levels

Main loop:
- fetch current open orders
- detect which ladder levels are missing compared with expected ladder
- derive filled sell / buy counts
- derive net shift
- recompute expected ladder window
- cancel levels outside the new ladder window
- place levels missing inside the new ladder window

但这段只是 future implementation note，不是当前 flow contract 的执行承诺。

当前 flow 真正承诺的只有：

- 判定顺序
- accepted decision boundary
- keep / rebuild / abnormal 的优先级关系

## 13. Out Of Scope

本文档当前不定义：

- ladder mode 的最终枚举名
- ladder state 的最终数据结构
- expected ladder diff 的最终算法细节
- partial repair flow
- rolling anchor 细节
- net-shift 的最终 reference 规则
- inventory-aware 行为
- maker/taker fallback
- 具体实现函数名与模块组织方式