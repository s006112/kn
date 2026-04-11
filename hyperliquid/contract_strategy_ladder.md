# Ladder Strategy Contract

本文档只描述 future multi-grid / ladder strategy 中成立的策略规则。

它依赖父合同 `contract_grid_engine.md` 已定义的 engine state model、主循环动作协议、rebuild success / failure contract 与 abnormal / exit contract。

本文档位于 engine contract 之下，并与当前 single-pair strategy contract 并列存在：

- 父合同：`contract_grid_engine.md`
- 并列子合同：
  - `contract_strategy_pair.md`
  - `contract_strategy_ladder.md`
  - `contract_strategy_anchor.md`


## 1. Purpose

当前 ladder strategy 的职责是：

- 在 engine 已接受的 saved state 之上，
  定义 multi-grid / ladder 自己的 keep / rebuild / abnormal 接受规则
- 把当前 live orders 解释为：
  - 合法 ladder continuation
  - ladder fill-driven transition
  - ladder stale / drift rebuild
  - abnormal
- 为 engine 提供唯一策略判定结果：
  - `("keep", None)`
  - `("rebuild", reference_price)`
  - `("rebuild", None)`
  - `("abnormal", None)`

本文档不定义 engine 通用循环，不定义交易所执行细节，也不定义日志输出策略。

## 2. Strategy Scope

当前 ladder strategy 只适用于以下对象：

- saved state 对应一组围绕某个 anchor/reference 构造出的多层 buy / sell levels
- live orders 用于表示该 ladder 在当前时刻的实际挂单快照
- 当前 strategy 关注的是 ladder 作为一个整体是否仍然满足 contract，
  而不是单一 pair 是否成立

当前 ladder strategy 将处理：

- 多层 grid
- 每侧多个预期 levels
- 当前 live ladder 与 expected ladder 的结构一致性
- ladder 级别的 keep / rebuild / abnormal 判定

当前 ladder strategy 不处理：

- engine shell
- cleanup / 下单执行 / 交易所 client
- 任意“部分修好一点”的临时修补逻辑
- 未被合同显式接受的模糊 ladder 状态
- 具体日志频率
- 资金管理优化、库存优化与 maker/taker 细节

## 3. Relationship To Single-Pair Strategy

single-pair strategy 是 ladder strategy 的特殊情形，不是反过来。

也就是说：

- 当 ladder depth per side = 1 时，
  ladder strategy 在对象模型上可退化为 single-pair structure
- 但 ladder contract 的对象模型和状态解释高于 single-pair contract
- future multi-grid / ladder extension 不应建立为“多个 single-pair contract 的松散拼接”，
  而应建立为“一个 ladder contract，其中 depth = 1 时退化为 pair case”

因此：

- `contract_strategy_pair.md` 继续保留，作为 depth = 1 的独立最小合同
- `contract_strategy_ladder.md` 作为更高层的一般合同存在

## 4. Ladder Parameter Model

当前 ladder strategy 至少承认以下核心参数：

- `M` = number of levels per side
- `G` = grid step
- `Ref` = current reference / anchor price
- `BUY_GRID_FACTOR`
- `SELL_GRID_FACTOR`

其中：

- `M >= 1`
- `G > 0`
- `BUY_GRID_FACTOR > 0`
- `SELL_GRID_FACTOR > 0`

`Ref` 表示当前 ladder 构造所围绕的参考锚点价格。

## 5. Ladder Level Construction Rule

当前 ladder 的理论价格层级按以下参数化规则构造：

For `k = 1` to `M`:

- `SellLevel[k] = Ref + G x k x SELL_GRID_FACTOR`
- `BuyLevel[k]  = Ref - G x k x BUY_GRID_FACTOR`

这一定义是当前 ladder contract 的基础 level construction rule。

### 5.1 Symmetric Special Case

当：

- `BUY_GRID_FACTOR = 1.0`
- `SELL_GRID_FACTOR = 1.0`

时，上述 ladder 退化为对称网格结构：

- `SellLevel[k] = Ref + G x k`
- `BuyLevel[k]  = Ref - G x k`

因此，原始对称 ladder 只是当前参数化 ladder 的特殊情形。

### 5.2 Single-Pair Special Case

当：

- `M = 1`

时，当前 ladder 退化为一组单买单 / 单卖单结构：

- `SellLevel[1] = Ref + G x SELL_GRID_FACTOR`
- `BuyLevel[1]  = Ref - G x BUY_GRID_FACTOR`

因此，在价格结构层面：

- current single-pair price structure
- ladder with `M = 1`

是同一个 family 下的 special case relationship。

### 5.3 Degeneration Boundary

但只有当以下语义同时也成立时，才可认为 ladder contract 在行为上 fully covers current single-pair strategy：

- level construction rule 与 pair contract 的 buy/sell factor 语义一致
- rebuild reference rule 与 pair contract 一致
- 单边 residual / stale / abnormal 规则被显式定义

也就是说：

- `M = 1` 先保证对象模型可以退化
- 是否 fully cover current single-pair behavior，
  还取决于状态转移和 rebuild contract 是否也完成退化对齐

## 6. Ladder Object Model

当前 ladder strategy 至少承认以下对象语义：

### 6.1 Ladder Anchor

表示当前 ladder 构造所围绕的价格锚点。

该锚点用于：

- 推导 expected buy levels
- 推导 expected sell levels
- 作为 rebuild 时可能使用的 reference basis

该锚点不是：

- 任意 live order 的简单中点
- 任意单笔订单价格
- 任意一侧 residual 的价格别名

### 6.2 Ladder Levels

表示 ladder 中理论上应存在的价格层级集合。

至少包括两类：

- expected buy levels
- expected sell levels

这些 levels 共同定义 ladder 的理论形状。

### 6.3 Live Ladder Snapshot

表示当前交易所 open orders 在 ladder contract 视角下的实时快照。

该快照用于与 expected ladder 比较，并判断：

- 是否仍是合法 ladder continuation
- 是否已发生可接受的 fill-driven transition
- 是否已经 drift / stale
- 是否应视为 abnormal

## 7. Ladder State Model

当前 ladder strategy 的 saved state 至少应承认以下字段语义：

- `mode`
- `anchor_price`
- `grid_step`
- `buy_grid_factor`
- `sell_grid_factor`
- `buy_levels`
- `sell_levels`

可选扩展字段包括但不限于：

- `depth_per_side`
- `expected_levels`
- `rebuild_policy`
- `inventory_mode`

### 7.1 Ladder `mode`

ladder strategy 的 `mode` 不再等同于 single-pair 的：

- `PAIR`
- `BUY_ONLY`
- `SELL_ONLY`

future ladder mode 至少应允许表达：

- 完整 ladder 有效态
- ladder 单边残缺态
- ladder 需重建态
- abnormal

但在 Step 1 中，本文档不强制具体 mode 枚举名。
具体枚举名可在后续实现合同中收敛。

### 7.2 Expected-vs-Live Separation

当前 ladder strategy 必须明确区分：

- saved expected ladder
- current live ladder snapshot

这两者不能混为一个对象。

其中：

- saved expected ladder 表示系统认为“应该存在什么”
- current live ladder snapshot 表示交易所“现在实际存在什么”

任何 ladder keep / rebuild / abnormal 判定，都必须建立在这两者的比较之上。

## 8. Accepted Live Shape Model

在 ladder contract 下，live orders 不再只按 single-pair 的：

- `1 BUY + 1 SELL`
- `1 BUY + 0 SELL`
- `0 BUY + 1 SELL`

来解释。

当前 ladder contract 只承认以下更高层语义：

### 8.1 Valid Ladder Continuation

表示：

- 当前 live orders 仍然属于该 ladder 的合法子集或完整集合
- 其缺失或偏移仍落在 ladder contract 显式允许的范围内
- 当前状态仍可解释为“同一个 ladder 的延续”

### 8.2 Ladder Fill-Driven Transition

表示：

- ladder 某些 expected levels 已被成交或移除
- 但该变化仍属于 ladder contract 允许的状态转移
- 因此应继续走 ladder contract 的 keep / rebuild 分支，
  而不是立即视为 abnormal

### 8.3 Ladder Stale / Drift State

表示：

- 当前 live orders 虽可能仍与 ladder 有部分关系
- 但其与 expected ladder 的偏差已超过合同允许边界
- 因此不再允许继续 `keep`
- 应进入 ladder rebuild

### 8.4 Abnormal Ladder State

表示：

- 当前 live orders 既不能解释为合法 ladder continuation，
  也不能解释为合同允许的 fill-driven transition，
  也不能解释为允许的 stale / drift rebuild 前状态
- 因此必须返回 abnormal 或直接退出父合同路径

## 9. Strategy Decision Contract

当前 ladder strategy 仍必须遵守父合同的唯一动作协议：

- `("keep", None)`
- `("rebuild", reference_price)`
- `("rebuild", None)`
- `("abnormal", None)`

ladder strategy 不得绕过 engine 自己执行下列动作：

- cleanup
- 下单
- rebuild execution
- abnormal exit

也就是说：

- ladder strategy 负责判定
- engine shell 负责执行

## 10. Ladder Decision Order

future ladder strategy 的内部判定顺序必须固定为：

1. 读取 current live ladder snapshot
2. 构造或解析 current live ladder shape
3. 将 current live ladder 与 saved expected ladder 做比较
4. 优先检查是否命中 ladder fill-driven transition
5. 再检查是否仍满足 ladder keep
6. 再检查是否命中 ladder stale / drift rebuild
7. 以上都不满足则返回 abnormal

当前合同只约束顺序，不约束具体实现函数名。

## 11. Keep Contract

只有同时满足以下条件时，ladder strategy 才允许返回 `("keep", None)`：

- 当前 live ladder snapshot 仍可被解释为 saved expected ladder 的合法延续
- live ladder 与 expected ladder 的偏差没有超过合同允许的边界
- 当前状态未命中更高优先级的 fill-driven transition contract
- 当前状态未命中 stale / drift rebuild contract

若任一条件不成立，则不得返回 `keep`。

## 12. Rebuild Contract

ladder strategy 允许触发 rebuild 的原因只包括以下两类：

### 12.1 Fill-Driven Rebuild

表示：

- 当前 live ladder 的变化可被解释为合同允许的成交驱动转移
- 但根据 ladder contract，下一步必须重建完整 ladder 或新的期望 ladder

此时 strategy 可返回：

- `("rebuild", reference_price)`，若合同要求沿用某个显式参考锚点
- `("rebuild", None)`，若合同要求交由 engine 使用 fresh reference

### 12.2 Stale / Drift Rebuild

表示：

- 当前 live ladder 已明显偏离 saved expected ladder
- 继续 `keep` 会违反当前 contract
- 因此必须 rebuild

此时 strategy 同样只能返回：

- `("rebuild", reference_price)`
- `("rebuild", None)`

当前 Step 1 不规定选择哪一种。
这留给后续更细合同决定。

## 13. Abnormal Contract

当以下任一情况成立时，ladder strategy 必须返回 `("abnormal", None)`：

- 当前 live orders 无法映射为 ladder contract 可接受的对象
- 当前 live ladder 与 expected ladder 的关系无法被合同解释
- 当前状态既不属于 keep，
  也不属于 fill-driven rebuild，
  也不属于 stale / drift rebuild
- 当前 live orders 出现多余、冲突、重复、或无法归类的结构，
  且合同没有显式允许该结构

当前 ladder contract 采用严格边界：

- 不接受“差不多像 ladder”
- 不接受“有点像上一轮 ladder”
- 不接受“部分看起来合理，但没有合同定义”的 live shape

## 14. Rebuild Policy Boundary

当前 Step 1 只承认 ladder rebuild policy 是 contract 的一部分，
但不规定具体 policy 实现。

后续实现可选择例如：

- full ladder rebuild
- shifted ladder rebuild
- rolling window rebuild

但在 Step 1 中，这些都只是 future policy options，
不是当前合同已承诺的行为。

因此：

- 本文档先定义“何时必须 rebuild”
- 不定义“rebuild 具体怎样执行”

## 15. Engine Boundary

ladder strategy 必须继续服从父合同 `contract_grid_engine.md`：

- engine shell 是唯一执行方
- strategy 只提供动作结果
- rebuild success / failure 的接受规则仍由 engine 父合同定义
- abnormal exit 仍由 engine 父合同定义

也就是说，future ladder 不应重新发明一套 engine shell。

## 16. Out Of Scope

本文档当前不定义：

- ladder mode 的最终枚举名
- ladder levels 的最终数据结构格式
- partial repair contract
- rolling anchor 规则
- net-shift 规则
- inventory-aware behavior
- maker/taker fallback
- 资金分配与 sizing 优化
- 具体日志内容与日志节流策略
- 具体实现文件、函数名或模块组织方式