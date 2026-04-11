# degeneration_pair_to_ladder_m1.md

本文档用于验证：

当前 single-pair strategy 是否可以被 ladder strategy 在 `depth_per_side = 1` 情况下完全吸收。

它不是设计文档，也不是实现文档。
它是一个“等价性验证清单”。

只有当本文件中所有等价关系成立时，才可以认为：

> single-pair strategy 已收敛为 ladder strategy 的 `M = 1` case

---

## 1. Purpose

本文件的职责是：

- 把 current pair strategy 的行为拆解为可验证的结构
- 用 ladder-first schema language 重新表达这些行为
- 检查在 `M = 1` 时，ladder strategy 是否能生成完全等价的行为
- 避免“看起来一样但语义不同”的假收敛

---

## 2. Scope

本文件只覆盖：

- single-pair strategy
- ladder strategy 在 `depth_per_side = 1` 的情况

本文件不覆盖：

- multi-grid (`M > 1`)
- rolling ladder
- partial repair / 局部修复
- inventory / sizing 优化
- maker / taker 执行策略

---

## 3. Equivalence Dimensions

必须验证以下四类等价性：

1. Level Structure Equivalence
2. Compare Result Equivalence
3. Decision Result Equivalence
4. Reference Rule Equivalence

任何一类不成立，都不允许宣称“pair 已被 ladder 吸收”。

---

## 4. Level Structure Equivalence

### 4.1 Pair Representation

current pair strategy 中的结构为：

- `buy_price`
- `sell_price`

并满足：


sell_price - buy_price == (buy_grid_factor + sell_grid_factor) * grid_step


---

### 4.2 Ladder Representation (`M = 1`)

在 ladder schema 中：

* `depth_per_side = 1`
* `expected_structure.buy_levels = [buy_price]`
* `expected_structure.sell_levels = [sell_price]`

---

### 4.3 Required Equivalence

必须满足：


pair.buy_price == ladder.buy_levels[0]
pair.sell_price == ladder.sell_levels[0]


以及：


ladder.expected_structure 完整等价于 pair 的 price structure


---

### 4.4 Failure Case

若存在以下情况，则不成立：

* ladder level 构造方式与 pair 不一致
* buy/sell level 顺序或含义不同
* price spacing 不一致

---

## 5. Compare Result Equivalence

### 5.1 Pair Compare Cases

current pair strategy 隐含以下 compare 结果：

#### A. PAIR keep

* 当前 live orders 仍为同一组 pair
* price 完全匹配 saved state

#### B. Fill-driven transition

* `PAIR → BUY_ONLY`
* `PAIR → SELL_ONLY`

#### C. Residual completion

* `BUY_ONLY → no orders`
* `SELL_ONLY → no orders`

#### D. BUY_ONLY residual keep

* 单边买残单仍合法

#### E. BUY_ONLY stale (anchor break)

* 单边买残单偏离 mid 太远

#### F. SELL_ONLY keep

* 单边卖残单始终 keep

#### G. Abnormal

* 任意未被接受的结构

---

### 5.2 Ladder Compare Representation (`M = 1`)

ladder compare 必须能够表达：

* `live_levels == expected_levels`
* `missing_levels`
* `extra_levels`
* `level shift / transition`
* `structure mismatch`

---

### 5.3 Required Equivalence

每一个 pair case 必须能映射为 ladder compare result：

| Pair Case              | Ladder Compare 必须能表达    |
| ---------------------- | ----------------------- |
| PAIR keep              | expected == live        |
| fill-driven transition | one side missing        |
| residual completion    | all levels missing      |
| BUY_ONLY keep          | partial valid structure |
| BUY_ONLY stale         | valid but drifted       |
| SELL_ONLY keep         | partial valid structure |
| abnormal               | invalid structure       |

---

### 5.4 Failure Case

若 ladder compare 无法区分以下情况，则失败：

* BUY_ONLY keep vs BUY_ONLY stale
* fill-driven vs abnormal
* SELL_ONLY keep vs abnormal

---

## 6. Decision Result Equivalence

### 6.1 Pair Decision Output

pair strategy 输出：


("keep", None)
("rebuild", reference_price)
("rebuild", None)
("abnormal", None)


---

### 6.2 Ladder Decision Output (`M = 1`)

ladder strategy 必须生成完全相同的 action protocol：

* 不允许新增 action 类型
* 不允许改变返回结构

---

### 6.3 Required Equivalence

每一个 pair decision 必须对应 ladder decision：

| Pair Decision               | Ladder 必须输出相同     |
| --------------------------- | ----------------- |
| PAIR keep                   | keep              |
| fill-driven rebuild         | rebuild(ref)      |
| residual completion rebuild | rebuild(ref)      |
| BUY_ONLY stale rebuild      | rebuild(None/ref) |
| SELL_ONLY keep              | keep              |
| abnormal                    | abnormal          |

---

### 6.4 Failure Case

以下任一情况都不成立：

* ladder decision 无法区分 rebuild(None) vs rebuild(ref)
* ladder decision 多出新 action
* ladder decision 需要额外状态才能表达 pair 行为

---

## 7. Reference Rule Equivalence

这是最容易被忽略、但最关键的一部分。

---

### 7.1 Pair Reference Rules

pair strategy 中 reference 来源包括：

#### A. Fill-driven rebuild

使用已成交一侧的价格：


SELL_ONLY → reference = previous buy_price
BUY_ONLY  → reference = previous sell_price


#### B. Residual completion

使用 residual side 保存的 price

#### C. Anchor break

使用：


reference = None → 重新用 mid 推导


---

### 7.2 Ladder Reference Rules (`M = 1`)

ladder strategy 必须在 `M = 1` 时：

* 使用同样的 reference 选择规则
* 不允许引入新的 reference 源
* 不允许改变 rebuild 触发后的 reference 语义

---

### 7.3 Required Equivalence

必须满足：

* fill-driven reference 完全一致
* residual completion reference 完全一致
* stale rebuild reference 语义一致
* `None` 的语义一致（重新推导 anchor）

---

### 7.4 Failure Case

以下任一情况都不成立：

* ladder 使用不同 reference 规则
* rebuild anchor 选择不同
* stale 情况 reference 不一致

---

## 8. Final Acceptance Criteria

只有当以下全部成立时：

* Level Structure Equivalence ✔
* Compare Result Equivalence ✔
* Decision Result Equivalence ✔
* Reference Rule Equivalence ✔

才允许声明：


single-pair strategy 已被 ladder strategy 的 M = 1 case 吸收


---

## 9. Non-Goals

本文件不用于：

* 设计 ladder strategy
* 定义 multi-grid 行为
* 修改 runtime
* 优化交易逻辑

它只用于：
> 防止你“以为已经收敛”，但实际上还没有
