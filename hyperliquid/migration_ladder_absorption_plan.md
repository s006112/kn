# migration_function_upgrade_roadmap.md

本文档定义 current single-pair runtime 向 ladder-first strategy architecture 迁移时的升级路线图。

它不是 contract，也不是 flow。
它的职责是：

- 给当前所有文档与代码模块标注“所处轨道”
- 把迁移过程拆成低认知负担的小步推进
- 明确每一步允许改什么、不允许改什么
- 确保最终 single-pair strategy 可以被收敛为 ladder strategy 的 `M = 1` case
- 避免 future ladder 文档直接倒灌 current runtime，造成语义混乱

---

## 1. Core Problem

当前系统处于一个正常但高认知负担的过渡状态：

- 文档层已经开始进入 V2
- schema language 已经 ladder-first
- future ladder contract / flow 已经开始定义 general ladder world
- 但代码层 runtime 仍然主要运行 current single-pair implementation

因此，当前最重要的目标不是“立刻统一所有层”，
而是建立一个可平滑推进的 migration roadmap，使：

- current runtime 继续稳定
- bridge language 持续补齐
- future ladder design 持续前进
- 最终 pair 行为可被 ladder `M = 1` 完整吸收

---

## 2. Migration Principle

本路线图采用三轨并行、不同速度推进：

### 2.1 Executable Now

这一轨的对象代表：

- 当前真实运行事实
- 当前 runtime truth
- 当前必须保持稳定、可验证、可回退的内容

### 2.2 Bridge In Progress

这一轨的对象代表：

- 当前与未来之间的 compatibility mapping
- 新语言、新对象模型、新命名系统
- 用于解释 current pair world 如何接入 ladder-first schema

### 2.3 Future Target

这一轨的对象代表：

- ladder-first general model
- future multi-grid / ladder strategy
- 暂未要求 current runtime 立即实现的目标模型

---

## 3. Current Track Classification

### 3.1 Executable Now

以下对象属于 current runtime truth：

- `contract_grid_engine.md`
- `contract_strategy_pair.md`
- `contract_strategy_anchor.md`
- `flow_engine.md`
- `flow_strategy_pair.md`
- `grid.py`
- `grid_logic.py`
- `grid_strategy.py` 中当前 single-pair decision kernel

这些对象的职责是：

- 维持 current pair runtime 的 contract / flow / code 对齐
- 保持主循环、rebuild、abnormal exit、pair keep、single-sided branch 等行为稳定
- 不为了 future ladder language 而提前破坏当前可运行事实

### 3.2 Bridge In Progress

以下对象属于 bridge layer：

- `contract_schema.md`

该对象当前的职责不是替代 current runtime truth，
而是提供 ladder-first 的上位 vocabulary，并允许 current pair runtime 通过 compatibility mapping 接入。

因此：

- 它是解释层
- 它是桥接层
- 它暂时不是 runtime contract truth

### 3.3 Future Target

以下对象属于 future target：

- `contract_strategy_ladder.md`
- `flow_strategy_ladder.md`

这些对象当前的职责是：

- 提前定义 general ladder world
- 提前定义 ladder 的 parse / compare / decision 顺序
- 为 future implementation 预留 contract 与 flow 结构

但在 ladder 尚未完成 `M = 1` degeneration proof 之前：

- 它们不是 current runtime truth
- 它们不能直接指挥 current execution code
- 它们只能作为 future target 存在

---

## 4. Global Migration Policy

在整个迁移完成之前，必须遵守以下总原则：

### 4.1 Freeze Engine Shell

`contract_grid_engine.md` 与 `flow_engine.md` 已经定义了 engine shell 的稳定边界。

因此，迁移早期阶段默认：

- 不修改 engine action protocol
- 不修改主循环 keep / rebuild / abnormal 的基本协议
- 不让 ladder future design 倒灌 engine shell

engine shell 应被视为当前 frozen shell。

### 4.2 Preserve Pair Contract

在 ladder 尚未完成对 pair `M = 1` 的完整吸收前：

- `contract_strategy_pair.md` 必须继续保留
- `flow_strategy_pair.md` 必须继续保留
- pair strategy 继续作为 current executable strategy 存在

不允许因为已经有 ladder contract，就假设 pair contract 可以直接删除。

### 4.3 Bridge Before Rewrite

迁移顺序必须先做：

1. vocabulary bridge
2. compatibility mapping
3. compare language alignment
4. pair-to-ladder degeneration proof
5. ladder implementation

不允许跳过 bridge 层直接 rewrite runtime。

### 4.4 One Change Type Per Round

每一轮改动，只允许属于以下三种任务类型之一：

#### Type A: Language Only
只改命名、schema、mapping、文档，不改 runtime 行为。

#### Type B: Pure Function Only
只改 parse / compare / decision 的纯函数，不改 engine shell，不改 execution。

#### Type C: Execution Only
只改 rebuild / place / cleanup / runtime wiring，不改 schema language。

任何一轮都不允许混合以上三种任务。

---

## 5. Migration Stages

---

## Stage 0 — Track Separation

### Goal

先把“当前运行事实”“桥接语言”“未来目标”分开。

### Do

- 明确标注每份文档与每个模块属于哪条轨道
- 停止把 future ladder doc 当成 current runtime truth
- 停止要求 current pair runtime 立即服从 V2 ladder-first language

### Do Not

- 不改 runtime 行为
- 不改 engine shell
- 不改 pair strategy action protocol
- 不引入 ladder implementation

### Output

- 本路线图文件本身
- 当前项目进入三轨道管理状态

### Exit Criteria

满足以下条件时，Stage 0 完成：

- 所有关键文件都已被归类
- 后续每轮任务都能先回答：“这轮是在改哪条轨道？”

---

## Stage 1 — Pair-to-Schema Compatibility Mapping

### Goal

让 current pair runtime 可以被 ladder-first schema language 解释。

### Do

新增一组 bridge pure functions 或 bridge document mapping：

- `map_pair_state_to_schema_state(...)`
- `map_orders_to_live_structure(...)`
- `compare_pair_under_schema(...)`
- `map_tuple_action_to_decision_result(...)`

### Meaning

这一步的核心不是改行为，
而是让当前 pair world 可以用以下 schema 语言重述：

- Strategy State
- Current Live Structure
- Structure Compare Result
- Strategy Decision Result

### Do Not

- 不改 `grid.py` 主循环
- 不改当前 tuple action protocol
- 不直接实现 ladder levels
- 不改交易所执行
- 不把 schema object 强制写入 runtime state

### Output

- 一份 pair → schema 的 compatibility mapping
- 或一组纯映射函数
- 或一份专门的 mapping 说明文档

### Exit Criteria

满足以下条件时，Stage 1 完成：

- current pair state 可以被表示成 schema state
- current live orders 可以被表示成 live structure
- current pair compare 结果可以被表示成 compare result
- current action tuple 可以被解释为 decision result

---

## Stage 2 — Internal Pair Strategy Refactor To Parse / Compare / Decision

### Goal

把 current single-pair strategy 的内部骨架改造成 ladder-first 可兼容形态，
但外部行为保持不变。

### Do

将 current pair strategy 内部重构为三个逻辑层：

- `parse_pair_live_structure(...)`
- `compare_pair_live_vs_expected(...)`
- `decide_pair_action_from_compare(...)`

### Meaning

这一步不是把 pair 变成 ladder，
而是把 pair strategy 的内部语言改造成 ladder future flow 可兼容的形式。

### Do Not

- 不改 engine shell
- 不改 action protocol
- 不改 exchange 执行逻辑
- 不支持 `M > 1`
- 不改变现有 keep / rebuild / abnormal 的运行结果

### Output

- 新版 single-pair strategy decision kernel
- 内部结构从“条件分支串联”升级为“parse → compare → decision”

### Exit Criteria

满足以下条件时，Stage 2 完成：

- `grid_strategy.py` 已真实承载 single-pair decision kernel
- pair strategy 内部已经显式拥有 parse / compare / decision 三层
- 外部 runtime 行为与当前实现保持等价

---

## Stage 3 — Pair To Ladder `M = 1` Degeneration Proof

### Goal

证明 single-pair strategy 不只是“概念上像 ladder”，
而是在 contract / compare / decision / reference 语义上，
都可以退化为 ladder strategy 的 `M = 1` case。

### Required Document

新增：

- `degeneration_pair_to_ladder_m1.md`

### Required Checklist

这份文档至少要检查四类等价性：

#### 3.1 Level Structure Equivalence

确认 current pair 的：

- `buy_price`
- `sell_price`

是否严格等价于 ladder 在 `depth_per_side = 1` 时的：

- `expected_structure.buy_levels[0]`
- `expected_structure.sell_levels[0]`

#### 3.2 Compare Result Equivalence

确认以下 current pair cases 是否都可被 ladder compare language 表示：

- pair keep
- fill-driven residual transition
- BUY_ONLY residual keep
- BUY_ONLY stale rebuild
- SELL_ONLY keep
- abnormal

#### 3.3 Decision Result Equivalence

确认 ladder decision result 在 `M = 1` 时，
是否能生成与 current pair strategy 相同的唯一动作：

- `("keep", None)`
- `("rebuild", reference_price)`
- `("rebuild", None)`
- `("abnormal", None)`

#### 3.4 Reference Rule Equivalence

确认当前 pair strategy 中用于 rebuild 的 reference rule，
在 ladder `M = 1` 时也完全成立，包括：

- fill-driven rebuild reference
- stale / drift rebuild reference
- abnormal fallback 边界

### Do Not

- 不急着写 ladder runtime code
- 不急着删除 pair contract
- 不急着让 ladder strategy 接管 engine

### Output

- `degeneration_pair_to_ladder_m1.md`

### Exit Criteria

只有当以上四类等价性全部成立时，才能声明：

- single-pair strategy 已被 ladder strategy 的 `M = 1` case 吸收

在此之前，不允许声称 pair 已 fully merged into ladder。

---

## Stage 4 — Ladder Pure Function Skeleton

### Goal

开始实现 ladder strategy，
但仍然只做纯函数层，不接 runtime execution。

### Do

新增 ladder pure function skeleton，例如：

- `parse_live_ladder_snapshot(...)`
- `compare_live_ladder_to_expected(...)`
- `decide_ladder_action(...)`

必要时再增加：

- `build_expected_ladder_structure(...)`
- `classify_ladder_family(...)`
- `detect_ladder_transition(...)`

### Meaning

这一阶段真正开始实现 future ladder strategy，
但仍然保持：

- 不碰 engine shell
- 不碰下单执行
- 不碰 cleanup pipeline

### Do Not

- 不接交易所
- 不接真实 rebuild execution
- 不在主循环中替换 current pair runtime
- 不实现 partial repair
- 不实现 inventory optimization
- 不实现 maker / taker policy

### Output

- ladder strategy 的纯函数骨架
- ladder parse / compare / decision 的第一版可测试实现

### Exit Criteria

满足以下条件时，Stage 4 完成：

- ladder strategy 可以脱离交易所独立测试
- ladder compare result / decision result 已可生成
- `M = 1` case 与 pair degeneration proof 不冲突

---

## Stage 5 — Ladder Runtime Integration

### Goal

在 engine shell 不大改的前提下，
让 ladder strategy 开始接入 current runtime。

### Do

- 保持 engine 继续只认 action protocol
- 通过 strategy selector 或 strategy adapter 接入 ladder strategy
- 让 ladder strategy 在不破坏 shell contract 的前提下参与 decision

### Possible Interface

例如：

- `strategy_kind = "pair"`
- `strategy_kind = "ladder"`

但 engine shell 不负责理解 ladder 内部结构。

### Do Not

- 不重写 engine shell contract
- 不同时修改 pair runtime 与 ladder runtime 的所有执行逻辑
- 不在 integration 同一轮里新增复杂优化逻辑

### Output

- ladder strategy 可被 engine 调用
- pair strategy 仍然可独立保留
- engine 通过统一 action protocol 调用两者

### Exit Criteria

满足以下条件时，Stage 5 完成：

- engine 可以在 pair / ladder strategy 间切换
- ladder strategy 接入 runtime 后不破坏 current shell contract
- pair strategy 仍可作为 fallback 保留

---

## Stage 6 — Pair Absorption

### Goal

在 `M = 1` degeneration proof 成立，
且 ladder runtime 已稳定后，
再逐步让 pair strategy 从“独立实现”变成“ladder 的特殊 case wrapper”。
若 compare / reference 规则仍未 fully proven，则保留保守的 pair-specific fallback。

### Current Status

当前 Stage 6 处于 `PARTIALLY COMPLETED / IN PROGRESS` 状态。

当前仓库已经完成的部分是：

- exact `PAIR` keep 已经开始复用 ladder `M = 1`
- `PAIR -> BUY_ONLY` fill-driven transition 已经开始复用 ladder `M = 1`
- `PAIR -> SELL_ONLY` fill-driven transition 已经开始复用 ladder `M = 1`
- 可安全表示的 saved `PAIR` abnormal path 已经开始复用 ladder `M = 1`
- pair strategy 已不再是这些 path 的唯一事实执行来源

当前仓库仍然明确保留为 pair-specific fallback 的部分是：

- `BUY_ONLY -> no orders`
- `SELL_ONLY -> no orders`
- `BUY_ONLY keep`
- `BUY_ONLY stale / anchor break`
- `SELL_ONLY keep`

保留这些 fallback 的原因仍然与 Stage 3 证明边界一致：

- compare equivalence 只达到 `PARTIALLY PROVEN`
- reference rule equivalence 只达到 `PARTIALLY PROVEN`
- residual completion side inference 仍依赖 saved `mode`
- `BUY_ONLY keep` 与 stale split 仍依赖外部 anchor logic

因此，当前 Stage 6 不能被描述为“pair 已 fully absorbed”。
它更准确地表示为：

- saved `PAIR` 的 proven-safe path 已经 ladder-M=1-backed
- residual semantics 仍保留 pair-specific fallback
- Step 6 只有在这些 fallback 被后续明确吸收，或被项目明确决定长期冻结时，
  才能视为最终完成

### Do

- 让 pair contract 保留一段过渡期
- 让 pair strategy 实现逐步调用 ladder `M = 1` path
- 在验证稳定后，再减少 pair-specific implementation surface

### Do Not

- 不提前删除 pair contract
- 不提前删除 pair flow
- 不在没有 degeneration proof 的情况下强行 merge

### Output

- pair 成为 ladder `M = 1` 的 special path
- 或 pair 只剩下 compatibility wrapper

当前仓库中的更精确输出状态是：

- pair 已经成为“部分 ladder-M=1-backed、部分 pair-specific fallback”的兼容 wrapper

### Exit Criteria

满足以下条件时，Stage 6 完成：

- pair 在已 proven-safe 的 path 上已经复用 ladder `M = 1`
- compare / reference 尚未 fully proven 的 path 仍保留明确的 pair-specific fallback
- current runtime 已不再只依赖 pair-only internal implementation 作为唯一事实来源
- pair-specific code 已经不是事实上的唯一执行来源

在当前状态下，这些条件只达到了“部分完成”的程度，
还不能被误读为 Step 6 已经彻底收口。

---

## 6. Function Upgrade Order

函数升级必须遵守以下顺序：

### 6.1 First Upgrade

先升级 bridge / pure-function 层：

- state mapping
- live structure mapping
- compare result mapping
- decision result mapping

### 6.2 Second Upgrade

再升级 pair strategy internal kernel：

- parse
- compare
- decide

### 6.3 Third Upgrade

再新增 ladder pure functions：

- ladder parse
- ladder compare
- ladder decide

### 6.4 Last Upgrade

最后才升级 execution wiring：

- strategy selector
- ladder runtime integration
- pair absorption

---

## 7. Recommended Next Action

当前阶段，最小、最平滑、最低认知负担的下一步不是 ladder implementation。

当前推荐下一步是：

### Next Action A

新增：

- `degeneration_pair_to_ladder_m1.md`

并只完成以下四栏：

- level structure equivalence
- compare result equivalence
- decision result equivalence
- reference rule equivalence

### Next Action B

若暂时不想碰 degeneration proof，
则至少先完成：

- `pair_to_schema_mapping.md`

用于明确 current pair runtime 如何接入 `contract_schema.md`

---

## 8. Success Definition

本迁移路线图的最终成功，不是“ladder 文档写好了”，
也不是“pair 代码删掉了”。

真正成功的定义是：

- current runtime 在整个过程中始终稳定
- bridge layer 可以准确解释 current pair world
- ladder-first schema 成为共同语言
- ladder strategy 在纯函数层先成立
- pair strategy 被证明可退化为 ladder `M = 1`
- 最终 pair 不再是独立世界，而是 ladder general model 的特殊情形

在到达这个状态之前：

- pair 继续保留
- ladder 继续发展
- bridge 继续补齐
- 三条轨道不可混淆
