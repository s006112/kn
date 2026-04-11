# degeneration_pair_to_ladder_m1.md

## 1. Purpose

本文件用于完成 Stage 3 的最小证明工作：

- 不实现 ladder runtime
- 不接入 engine
- 不改变 current pair runtime 行为
- 只验证 current single-pair strategy 是否能被 restrained 地表示为 ladder `depth_per_side = 1`

这里的目标不是宣称“架构已经完成”，
而是把已经证明的部分和仍未证明的边界明确写出来。

---

## 2. Scope

本证明只允许使用以下三条轨道：

- Executable Now 作为 runtime truth：
  - `contract_strategy_pair.md`
  - `contract_strategy_anchor.md`
  - `flow_strategy_pair.md`
  - `grid_strategy.py`
- Bridge In Progress 作为证明语言：
  - `contract_schema.md`
  - `grid_strategy_schema_adapter.py`
- Future Target 只作为目标语义模型：
  - `contract_strategy_ladder.md`
  - `flow_strategy_ladder.md`

本文件不覆盖：

- ladder runtime
- engine wiring
- `M > 1`
- partial repair
- inventory / maker-taker policy
- Stage 4 之后的实现问题

---

## 3. Proof Method

本证明使用三层证据：

1. 当前 pair 事实  
   来自 `grid_strategy.py` 的 `parse -> compare -> decide` 纯函数，以及 pair / anchor contract 与 flow。

2. bridge 表达  
   使用 `grid_strategy_schema_adapter.py` 把 current pair saved state 与 live orders 表达为 ladder-first schema：
   - `expected_structure`
   - `live structure`
   - `compare result`
   - `decision result`

3. 机械化验证  
   使用离线脚本：
   - `python3 verify_pair_to_ladder_m1.py`

该脚本：

- 运行 9 个代表性 case
- 调用 current pair pure strategy
- 通过 schema adapter 重述同一 case
- 用一个明确标注为 proof-only / non-runtime / non-generalized-beyond-M=1 的局部 M=1 模型检查 action 对齐

判定规则严格采用本阶段要求：

- exact match -> `PROVEN`
- representable but missing contract detail -> `PARTIALLY PROVEN`
- cannot represent faithfully -> `NOT PROVEN`

---

## 4. Level Structure Equivalence

### Pair-side fact

current pair strategy 的保存态结构核心是：

- `buy_price`
- `sell_price`

pair keep 与 fill-driven rebuild 都以这两个价格作为 saved expected pair structure 的身份。

### Ladder M=1 target representation

在 ladder-first schema / ladder `M = 1` 语义下，对应目标表示为：

- `parameters.depth_per_side = 1`
- `expected_structure.buy_levels = [buy_price]`
- `expected_structure.sell_levels = [sell_price]`

### Verdict

`PROVEN`

### Reason

`grid_strategy_schema_adapter.py` 对 current pair saved state 的映射是直接且无损的：

- `buy_price -> expected_structure.buy_levels[0]`
- `sell_price -> expected_structure.sell_levels[0]`

`verify_pair_to_ladder_m1.py` 对 9 个 case 全部得到相同结论：  
schema state 始终保留“一侧一层”的 expected structure，不需要任何 ladder runtime 代码即可表达 current pair 的 level structure。

因此，Stage 3 在“价格层级结构本身”这一维度上，已经可以确认：

- current pair structure
- ladder `depth_per_side = 1`

在 expected level 表达上是等价的。

---

## 5. Compare Result Equivalence

### Pair-side fact

current pair compare layer 实际区分的是：

- fill-driven rebuild candidate
- `PAIR` keep candidate
- single-sided branch candidate
- abnormal candidate

其中一个重要边界是：

- `BUY_ONLY keep`
- `BUY_ONLY stale`

在 current code 中并不是两个不同的 compare object；  
它们共享同一个 `BUY_ONLY` single-sided branch candidate，随后在 decide 阶段再通过 anchor break 分裂。

### Ladder M=1 target representation

ladder-first compare language 期望表达：

- exact keep
- fill-driven transition
- stale / drift candidate
- abnormal

并通过：

- `expected levels`
- `live levels`
- `missing / extra / matched`
- `relation flags`

来承载这些语义。

### Verdict

`PARTIALLY PROVEN`

### Reason

机械化验证的结论如下：

- `PROVEN`
  - `PAIR keep`
  - `PAIR -> BUY_ONLY`
  - `PAIR -> SELL_ONLY`
  - `SELL_ONLY keep`
  - `abnormal`

- `PARTIALLY PROVEN`
  - `BUY_ONLY -> no orders`
  - `SELL_ONLY -> no orders`
  - `BUY_ONLY keep`
  - `BUY_ONLY stale`

具体原因分两类：

1. residual completion 的 compare 表达不够细  
   对于 saved `BUY_ONLY` 或 `SELL_ONLY` 且 live `EMPTY` 的 case，
   schema compare 能表达“这是 fill transition family”，
   但 `missing_levels` 会同时显示 buy 和 sell 两侧都缺失。  
   因此它需要依赖 saved `mode` 才能恢复“究竟是哪一侧 residual 完成了”，
   单靠 compare summary 还不足以精确重现 current pair 的 completion 语义。

2. `BUY_ONLY keep` 与 `BUY_ONLY stale` 的 compare 分界不独立  
   schema compare 可以表达 `BUY_ONLY` residual family，
   也可以允许 stale/drift 这一上位概念，
   但当前 bridge compare object 本身并不能单独把：
   - “继续 keep 的 BUY residual”
   - “触发 anchor break 的 BUY residual”
   
   拆开。  
   该分裂仍依赖外部 BTC mid 与 anchor break 规则。

所以，本阶段可以说：

- current pair cases 已经可以被 ladder-first compare language 解释

但还不能说：

- compare result object 已经在所有 required case 上完全等价

---

## 6. Decision Result Equivalence

### Pair-side fact

current pair strategy 对外只允许四种 tuple action：

- `("keep", None)`
- `("rebuild", reference_price)`
- `("rebuild", None)`
- `("abnormal", None)`

### Ladder M=1 target representation

`contract_schema.md` 与 future ladder contract / flow 允许的 decision result / action protocol 也是同一组动作：

- `keep`
- `rebuild` with explicit reference
- `rebuild` with fresh reference
- `abnormal`

### Verdict

`PROVEN`

### Reason

`verify_pair_to_ladder_m1.py` 中的 proof-only M=1 model 在 9 个代表性 case 上全部复现了 current pair action：

- `PAIR keep -> ("keep", None)`
- `PAIR -> BUY_ONLY -> ("rebuild", sell_price)`
- `PAIR -> SELL_ONLY -> ("rebuild", buy_price)`
- `BUY_ONLY -> no orders -> ("rebuild", buy_price)`
- `SELL_ONLY -> no orders -> ("rebuild", sell_price)`
- `BUY_ONLY keep -> ("keep", None)`
- `BUY_ONLY stale -> ("rebuild", None)`
- `SELL_ONLY keep -> ("keep", None)`
- `abnormal -> ("abnormal", None)`

因此，在“strategy 最终返回给 engine 的动作结果”这一维度上，
ladder `M = 1` 已经可以给出与 current pair 相同的 action result。

这证明的是 action result equivalence。  
它不自动证明 reference rule 已完全固定，后者单独见第 7 节。

---

## 7. Reference Rule Equivalence

### Pair-side fact

current pair rebuild reference 语义分为三类：

1. fill-driven rebuild  
   使用已成交那一侧的 saved price：
   - `PAIR -> BUY_ONLY` 时使用 `sell_price`
   - `PAIR -> SELL_ONLY` 时使用 `buy_price`

2. residual completion rebuild  
   使用 residual side 的 saved price：
   - `BUY_ONLY -> no orders` 使用 `buy_price`
   - `SELL_ONLY -> no orders` 使用 `sell_price`

3. stale / anchor-break rebuild  
   使用 `("rebuild", None)`，交由 engine 用 fresh mid reference 重建

此外：

- abnormal fallback 不携带 reference

### Ladder M=1 target representation

ladder `M = 1` 至少需要能表达：

- fill-driven rebuild reference
- stale / drift rebuild reference
- abnormal fallback boundary

### Verdict

`PARTIALLY PROVEN`

### Reason

这一维度没有完全闭合，原因也分三类：

1. fill-driven explicit reference 目前只做到“可表示”，未做到“合同已固定”  
   bridge layer 可以把 current pair 的显式 rebuild 价格表示为：
   - `reference.kind = placement_anchor`
   - `reference.price = ...`
   
   proof harness 也确实复现了：
   - `PAIR -> BUY_ONLY -> rebuild(110.0)`
   - `PAIR -> SELL_ONLY -> rebuild(90.0)`
   
   但 future ladder contract 目前只允许“fill-driven rebuild 可以返回 explicit reference”，
   尚未明确规定这个 explicit reference 必须就是“filled-side saved level”。

2. residual completion reference 仍依赖 saved mode 解读  
   `BUY_ONLY -> no orders` 与 `SELL_ONLY -> no orders` 可以复现正确 rebuild price，
   但 compare summary 本身会把两侧都显示为 missing。  
   也就是说，reference 选择仍需要依赖 saved `mode`，
   还不是 compare object 自身就能无歧义推出的结果。

3. stale / drift rebuild 的 `None` 语义尚未被 ladder contract 固定  
   current pair 的 `BUY_ONLY stale` 明确要求：
   - `("rebuild", None)`
   
   future ladder contract / flow 目前只说 stale / drift rebuild 可以返回：
   - `("rebuild", reference_price)` 或
   - `("rebuild", None)`
   
   但并未把这一 case 锁定为 fresh-reference rebuild。

本节中唯一已经闭合的子点是 abnormal boundary：

- pair abnormal -> `("abnormal", None)`
- ladder abnormal -> `("abnormal", None)`

这部分是 `PROVEN`。  
但整个 reference rule 维度仍只能给出 `PARTIALLY PROVEN`。

---

## 8. Case Matrix

| Pair case | Pair interpretation | Ladder M=1 representation | Compare status | Decision status | Reference status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `PAIR keep` | saved `PAIR` and live pair still match exactly | exact keep: `live_levels == expected_levels` | `PROVEN` | `PROVEN` | `PROVEN` | one buy level and one sell level match exactly |
| `PAIR -> BUY_ONLY` | sell filled, buy residual remains | fill transition with missing sell level | `PROVEN` | `PROVEN` | `PARTIALLY PROVEN` | explicit rebuild ref is representable, but ladder contract has not fixed it to filled-side saved level |
| `PAIR -> SELL_ONLY` | buy filled, sell residual remains | fill transition with missing buy level | `PROVEN` | `PROVEN` | `PARTIALLY PROVEN` | same gap as above on explicit rebuild reference source |
| `BUY_ONLY -> no orders` | buy residual fully completed | fill transition family: saved `BUY_ONLY` + live `EMPTY` | `PARTIALLY PROVEN` | `PROVEN` | `PARTIALLY PROVEN` | compare summary marks both sides missing; residual side must be recovered from saved `mode` |
| `SELL_ONLY -> no orders` | sell residual fully completed | fill transition family: saved `SELL_ONLY` + live `EMPTY` | `PARTIALLY PROVEN` | `PROVEN` | `PARTIALLY PROVEN` | same saved-mode dependency as above |
| `BUY_ONLY keep` | single buy residual remains valid | one-level residual continuation | `PARTIALLY PROVEN` | `PROVEN` | `PROVEN` | compare object alone does not split keep vs stale |
| `BUY_ONLY stale` | single buy residual breaks anchor threshold | stale / drift rebuild in M=1 | `PARTIALLY PROVEN` | `PROVEN` | `PARTIALLY PROVEN` | stale split requires external BTC mid; ladder contract does not yet require `rebuild(None)` |
| `SELL_ONLY keep` | single sell residual remains accepted | one-level sell residual continuation | `PROVEN` | `PROVEN` | `PROVEN` | current pair semantics ignore sell residual drift, and M=1 proof model can mirror that keep result |
| `abnormal` | live structure is outside accepted pair contract | abnormal candidate / abnormal fallback | `PROVEN` | `PROVEN` | `PROVEN` | strict abnormal boundary is preserved |

---

## 9. Final Verdict

full absorption is **not proven** at Stage 3.

更准确地说：

- Level Structure Equivalence: `PROVEN`
- Compare Result Equivalence: `PARTIALLY PROVEN`
- Decision Result Equivalence: `PROVEN`
- Reference Rule Equivalence: `PARTIALLY PROVEN`

因此，本阶段可以被保守地表述为：

- current single-pair strategy 已经可以被 ladder-first language 以 `M = 1` 的方式具体表示
- 并且 action result 层面已有可机械验证的 M=1 degeneration evidence

但本阶段**还不能**宣称：

- pair strategy 已被 ladder strategy 的 `M = 1` case fully absorbed

原因是：

- compare result 在 residual completion 与 `BUY_ONLY keep/stale` 上仍非完全无歧义
- reference rule 在 fill-driven reference source 与 stale rebuild `None` 语义上仍未由 future ladder contract 固定

---

## 10. Not Yet Proven / Deferred Items

以下内容仍未在 Stage 3 证明完成：

- compare object 如何在不依赖 saved `mode` 的情况下精确表达 residual completion side
- compare layer 如何独立区分 `BUY_ONLY keep` 与 `BUY_ONLY stale`
- future ladder contract 是否明确规定 fill-driven explicit reference 必须沿用 filled-side saved level
- future ladder contract 是否明确规定 `BUY_ONLY stale` 必须是 `("rebuild", None)`
- ladder runtime
- ladder pure function skeleton（Stage 4）
- engine integration
- `M > 1`
- partial repair
- inventory optimization
- maker / taker policy

本文件的结论仅支持：

- Stage 3 proof language completion

不支持：

- Stage 4 runtime implementation claims
- pair contract removal
- ladder 接管 current main loop
