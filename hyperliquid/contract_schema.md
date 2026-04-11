# Contract Schema.md

本文档定义 pair strategy 与 future ladder strategy 共用的上位 schema language。

它不定义 engine shell、flow 顺序、下单执行或日志策略。
它只定义 strategy layer 中最小必要的对象边界与命名收敛规则。

## 1. Purpose

当前 schema 的职责是：

- 统一 pair 与 ladder 的 object vocabulary
- 统一 expected structure 与 live structure 的分离
- 统一 compare result 与 decision result 的表达
- 为 future ladder implementation 提供上位 schema
- 允许 current pair runtime 先通过 compatibility mapping 接入，而不立刻重构代码

## 2. Core Principle

本 schema 采用 ladder-first 收敛方向：

- ladder 是 general model
- pair 是 `M = 1` 的 specialization
- pair 不作为 ladder 的上位模型存在

因此，future 应以 ladder object model 为主，
再让 current pair world 逐步映射进去。

## 3. Four Object Families

strategy layer 统一为四类对象：

### 3.1 Strategy State

表示当前已接受的 saved state。

最小字段：

- `strategy_kind`
- `mode`
- `reference.kind`
- `reference.price`
- `parameters.grid_step`
- `parameters.buy_grid_factor`
- `parameters.sell_grid_factor`
- `parameters.depth_per_side`
- `expected_structure.buy_levels`
- `expected_structure.sell_levels`

说明：

- pair 中 `depth_per_side = 1`
- ladder 中 `depth_per_side = M`

### 3.2 Current Live Structure

表示当前 open orders 解析后的 live structure。

最小字段：

- `strategy_kind`
- `buy_levels`
- `sell_levels`
- `order_count.buy`
- `order_count.sell`
- `order_count.total`
- `shape_hint`

说明：

- 它只表达当前 live structure
- 不表达最终 contract judgement
- 不可与 saved expected structure 混用

### 3.3 Structure Compare Result

表示 current live structure 与 saved expected structure 的比较结果。

最小字段：

- `expected.buy_levels`
- `expected.sell_levels`
- `live.buy_levels`
- `live.sell_levels`
- `missing.buy_levels`
- `missing.sell_levels`
- `extra.buy_levels`
- `extra.sell_levels`
- `matched.buy_levels`
- `matched.sell_levels`
- `relation.family_match`
- `relation.is_exact_keep_candidate`
- `relation.is_fill_transition_candidate`
- `relation.is_stale_candidate`
- `relation.is_abnormal_candidate`
- `summary.filled_buy_count`
- `summary.filled_sell_count`
- `summary.net_shift`

说明：

- 这是 pair / ladder 真正应收敛的核心对象
- pair 只是每侧 level 数量退化为 1
- ladder 则扩展为多层

### 3.4 Strategy Decision Result

表示 strategy 输出给 engine 的唯一动作结果。

最小字段：

- `action`
- `reference.kind`
- `reference.price`
- `reason.category`

允许动作固定为：

- `keep`
- `rebuild`
- `abnormal`

与 engine tuple 的映射为：

- `keep` -> `("keep", None)`
- `rebuild` 且 `reference.price` 有值 -> `("rebuild", reference.price)`
- `rebuild` 且 `reference.price` 为空 -> `("rebuild", None)`
- `abnormal` -> `("abnormal", None)`

## 4. Canonical Reference Rule

schema 中统一使用：

- `reference.kind`
- `reference.price`

这样可保留不同 reference 的语义差异，例如：

- `pair_center`
- `placement_anchor`
- `ladder_anchor`

因此，current pair world 中的：

- `pair_center_price`
- `reference_price`

可以被统一表达，
但不能被视为同一个无类型字段。

## 5. Current Pair Compatibility Mapping

当前 pair runtime 可先映射到 canonical schema，而不立即改代码。

### 5.1 Pair Snapshot State

当前字段：

- `mode`
- `buy_price`
- `sell_price`
- `pair_center_price`

映射后应理解为：

- `strategy_kind = pair`
- `reference.kind = pair_center`
- `reference.price = pair_center_price`
- `parameters.depth_per_side = 1`
- `expected_structure.buy_levels = [buy_price]`
- `expected_structure.sell_levels = [sell_price]`

### 5.2 Pair Rebuild State

当前字段：

- `mode`
- `buy_price`
- `sell_price`
- `reference_price`

映射后应理解为：

- `strategy_kind = pair`
- `reference.kind = placement_anchor`
- `reference.price = reference_price`
- `parameters.depth_per_side = 1`
- `expected_structure.buy_levels = [buy_price]`
- `expected_structure.sell_levels = [sell_price]`

## 6. Ladder Canonical Form

future ladder state 应直接使用 canonical ladder form。

最小解释为：

- `strategy_kind = ladder`
- `reference.kind = ladder_anchor`
- `reference.price = Ref`
- `parameters.grid_step = G`
- `parameters.buy_grid_factor = BUY_GRID_FACTOR`
- `parameters.sell_grid_factor = SELL_GRID_FACTOR`
- `parameters.depth_per_side = M`
- `expected_structure.buy_levels = [...]`
- `expected_structure.sell_levels = [...]`

其 level construction rule 为：

For `k = 1` to `M`:

- `SellLevel[k] = Ref + G x k x SELL_GRID_FACTOR`
- `BuyLevel[k]  = Ref - G x k x BUY_GRID_FACTOR`

因此：

- `M = 1` 时，可退化为 single-pair price structure
- `BUY_GRID_FACTOR = SELL_GRID_FACTOR = 1.0` 时，可退化为 symmetric ladder special case

## 7. Pair And Ladder Under One Schema

在这份 schema 下：

- pair = `strategy_kind = pair` 且 `depth_per_side = 1`
- ladder = `strategy_kind = ladder` 且 `depth_per_side = M`

pair 当前的：

- exact keep
- fill-driven rebuild
- single-sided residual keep
- anchor stale rebuild

都可先被解释为 `M = 1` specialization 下的行为语义。

ladder 当前的：

- exact keep
- fill-driven transition
- stale / drift rebuild

则是同一上位 schema 在多层结构下的展开。

## 8. Meta Vocabulary

后续 pair / ladder 文档建议统一使用以下 vocabulary：

- `saved expected structure`
- `current live structure`
- `structure compare result`
- `exact keep`
- `fill-driven transition`
- `stale / drift rebuild`
- `abnormal`
- `reference`
- `anchor`
- `expected levels`
- `live levels`
- `missing levels`
- `extra levels`

## 9. Minimal Adoption Order

建议顺序：

1. 先采用这份 schema vocabulary
2. 再让 pair flow 改写为 ladder-first wording
3. 再做 ladder pure function skeleton
4. 最后才决定 runtime field 是否迁移到 canonical shape

## 10. Out Of Scope

本文档不定义：

- engine shell flow
- rebuild execution
- cleanup
- compare algorithm 细节
- net-shift 公式细节
- rolling anchor policy
- loading / inventory handling policy
- logging format
- 具体函数名与模块组织方式