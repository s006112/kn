# Single Pair Grid Engine Contract

## 1. 合同范围

本合同描述 `hyper` 当前 single-pair REST polling grid engine 的已实现行为。

权威实现文件：

- `hyper/grid.py`
- `hyper/grid_config.py`
- `hyper/grid_decision.py`
- `hyper/grid_execution.py`

当前系统只定义：

- 单币对：`SYMBOL = "UBTC/USDC"`
- 单组 grid pair 或其合法单边 residual
- REST 读取与固定 polling 主循环
- `keep` / `rebuild` / `abnormal` 三类循环结果

本合同不定义：

- 多 pair
- 多层 ladder
- websocket 或 event-driven 模型
- portfolio 级状态机
- transport abstraction
- 自动异常修复
- 合同外的隐式策略状态

## 2. 模块职责

### `grid.py`

负责 engine lifecycle：

- 在导入其他 grid 模块前调用 `apply_runtime_overrides`
- `bootstrap`
- `run_cycle`
- `main`
- 创建 `Info`
- 创建 `Exchange` 并赋值给 execution client 变量 `trader`
- 持有并更新唯一 `saved_state`

### `grid_config.py`

负责配置、读取边界与通用 helper：

- env/account 配置
- mode 常量
- price helper
- logging helper
- `normalize_order`
- `read_orders`
- `read_btc_mid`
- `read_btc_grid`
- `retry_read`
- `summarize_orders`

### `grid_decision.py`

负责纯决策：

- `classify_order_mode`
- `decide_cycle_action`

该模块不做 IO，不下单，不修改 `saved_state`。

### `grid_execution.py`

负责执行侧行为：

- `build_pair`
- `classify_order_result`
- `place_limit_order`
- `place_pair`
- `cleanup_orders`
- `cleanup_after_partial_place_failure`
- `place_fill_replace_pair`
- `rebuild`

该模块不决定主循环是否继续；成功返回新 state，失败返回 `None` 或抛出未捕获异常。

## 3. 运行时配置

`grid.py` 在导入 `grid_decision`、`grid_execution` 等模块前执行：

- `GRID_STEP = 200.0`
- `BUDGET_USDC = 250.0`
- `BUY_GRID_FACTOR = 1.0`
- `SELL_GRID_FACTOR = 1.0`

因此通过 `grid.py` 标准入口运行时，上述值是当前 grid engine 的有效参数。

其他当前配置：

- `ALLOW_BUY_ONLY_WHEN_NO_BTC = True`
- `ALLOW_SELL_ONLY_WHEN_NO_USDC = True`
- `REANCHOR_BREAK = True`
- `REANCHOR_BREAK_STEPS = 2`
- `MAIN_LOOP_POLL_INTERVAL_SEC = 1.5`
- `WAIT_NO_OPEN_ORDERS_INTERVAL_SEC = 0.5`
- `MAX_RETRIES = 4`
- `RETRY_SEC = 0.5`
- `BTC_MID_KEY = "@142"`

## 4. State Model

允许作为 `saved_state["mode"]` 继续运行的模式只有：

- `PAIR_MODE = "PAIR"`
- `BUY_ONLY_MODE = "BUY_ONLY"`
- `SELL_ONLY_MODE = "SELL_ONLY"`

`ABNORMAL_MODE = "ABNORMAL"` 只用于分类结果，不允许作为继续运行的 saved state。

### Live classification state

`classify_order_mode` 返回当前 live orders 的分类 state：

- `mode`
- `buy_price`，仅 `PAIR` / `BUY_ONLY` 包含
- `sell_price`，仅 `PAIR` / `SELL_ONLY` 包含

该 state 是当前 live orders 的快照，不包含 `reference_price`。`ABNORMAL` state 只包含 `mode`。

### `PAIR` rebuild state

`make_pair_state` 返回：

- `mode`
- `buy_price`
- `sell_price`
- `reference_price`

### `BUY_ONLY` rebuild state

`make_buy_only_state` 返回：

- `mode`
- `buy_price`
- `reference_price`

### `SELL_ONLY` rebuild state

`make_sell_only_state` 返回：

- `mode`
- `sell_price`
- `reference_price`

约束：

- `reference_price` 只表示 rebuild 使用的 anchor。
- 当前实现不定义额外中心价状态。
- `BUY_ONLY` state 不依赖 `sell_price`。
- `SELL_ONLY` state 不依赖 `buy_price`。

## 5. Price Helpers

当前价格 helper 为：

- `normalize_price(price)`：`round(float(price))` 后转为 `float`
- `format_price(price)`：归一化后转为整数字符串
- `price_gap_matches(buy_price, sell_price, expected_gap)`：归一化后检查 gap 精确相等
- `price_distance_at_least(high_price, low_price, distance)`：归一化后检查距离大于等于阈值

关键价格语义：

- pair price equality 直接比较 live classification state 与 saved state 中的价格字段。
- pair gap 使用 `price_gap_matches`。
- anchor break 距离使用 `price_distance_at_least`。
- `price_gap_matches` 当前强制 normalized gap equality。

## 6. Read Boundary

### `normalize_order`

`normalize_order(order)` 的行为：

- raw side `"B"` 归一化为 `"BUY"`
- raw side `"A"` 归一化为 `"SELL"`
- 其他 raw side 抛出 `ValueError`
- 保留原 order 其他字段
- 设置 `price = normalize_price(order["limitPx"])`

### `read_orders`

`read_orders(info)` 通过：

- `retry_read("open-orders", lambda: info.open_orders(ACCOUNT_ADDRESS))`
- 对每个返回 order 调用 `normalize_order`

返回值是 normalized order list。

### `read_btc_mid`

`read_btc_mid(info)` 通过 `retry_read("btc-mid", info.all_mids)` 读取 mids。

行为：

- 从 `BTC_MID_KEY` 读取 BTC mid。
- 缺失时按 `0` 处理。
- `btc_mid <= 0` 时返回 `None`。
- 正数 mid 返回 `normalize_price(btc_mid)`。

### `read_btc_grid`

`read_btc_grid(info)`：

- 调用 `read_btc_mid(info)`。
- 若返回 `None`，抛出 `ValueError("Failed to get BTC mid price")`。
- 否则按 `int((btc_mid / GRID_STEP) + 0.5) * GRID_STEP` 取最近 grid。
- 返回 `normalize_price(...)`。

### `retry_read`

`retry_read(name, fn, retries=MAX_RETRIES, delay=RETRY_SEC)` 当前重试：

- HTTP status `429`
- HTTP status `502`
- HTTP status `503`
- HTTP status `504`
- `requests.exceptions.Timeout`
- `requests.exceptions.ConnectionError`
- `TimeoutError`
- `ConnectionResetError`
- `socket.timeout`
- `OSError` 且异常文本包含 `timeout`、`connection` 或 `temporarily`

非 retryable exception 直接抛出。

retryable exception 在最后一次失败后记录日志并重新抛出。

## 7. Mode Classification

`classify_order_mode(orders)` 返回统一的 live classification state：

- `{"mode": PAIR_MODE, "buy_price": buy_price, "sell_price": sell_price}`
- `{"mode": BUY_ONLY_MODE, "buy_price": buy_price}`
- `{"mode": SELL_ONLY_MODE, "sell_price": sell_price}`
- `{"mode": ABNORMAL_MODE}`

### `PAIR`

合法 `PAIR` 必须满足：

- `len(orders) == 2`
- 恰好可取得一个 `"BUY"` order price
- 恰好可取得一个 `"SELL"` order price
- `buy_price is not None`
- `sell_price is not None`
- `buy_price < sell_price`
- `price_gap_matches(buy_price, sell_price, (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP)` 为真

合法 `PAIR` 返回的 live state：

- `mode = PAIR_MODE`
- `buy_price`
- `sell_price`

### `BUY_ONLY`

当 `len(orders) == 1` 且该 normalized order 的 `side == "BUY"` 时，分类为 `BUY_ONLY_MODE`，并返回 `buy_price`。

### `SELL_ONLY`

当 `len(orders) == 1` 且该 normalized order 的 `side != "BUY"` 时，分类为 `SELL_ONLY_MODE`，并返回 `sell_price`。

在 `read_orders` 归一化边界下，单 order side 只应为 `"BUY"` 或 `"SELL"`。

### `ABNORMAL`

其他情况分类为 `ABNORMAL_MODE`，包括：

- `len(orders) == 0`
- `len(orders) > 2`
- 两张单无法取得一买一卖
- 两张单价格顺序非法
- 两张单 normalized gap 不等于 expected gap

## 8. Bootstrap

`bootstrap()` 当前流程：

1. 创建 `info = Info(constants.MAINNET_API_URL)`。
2. 创建 `trader = Exchange(Account.from_key(API_WALLET_KEY), constants.MAINNET_API_URL, account_address=ACCOUNT_ADDRESS)`。
3. 调用 `read_orders(info)`。
4. 调用 `summarize_orders(orders)`。
5. 调用 `classify_order_mode(orders)`。
6. 若 `current_state["mode"] == PAIR_MODE`，返回 `(current_state, info, trader)`。
7. 否则调用 `rebuild(info, trader, orders)`。
8. 若 rebuild 返回 `None`，记录 `Bootstrap Rebuild Failed` 并返回 `(None, info, trader)`。
9. 若 rebuild 成功，返回 `(state, info, trader)`。

bootstrap 只直接接受合法 `PAIR`。

bootstrap 不直接接受：

- `BUY_ONLY`
- `SELL_ONLY`
- `ABNORMAL`

bootstrap 没有通用 exception shell；未捕获异常会向调用方传播。

## 9. Main Loop

### `run_cycle`

`run_cycle(info, trader, saved_state)` 当前流程：

1. 每轮调用 `read_orders(info)`。
2. 默认 `btc_mid = None`。
3. 仅当 `saved_state["mode"] == BUY_ONLY_MODE` 时调用 `read_btc_mid(info)`。
4. 调用 `decide_cycle_action(orders, saved_state, btc_mid)`。
5. 若 action 为 `"keep"`，返回原 `saved_state`。
6. 若 action 为 `"rebuild"`，调用 `rebuild(info, trader, orders, rebuild_price)` 并返回其结果。
7. 若 rebuild 返回 `None`，记录 `rebuild failed`。
8. 其他 action 视为 abnormal：输出 order 摘要，记录 `abnormal`，返回 `None`。

### `main`

`main()` 当前流程：

1. 调用 `bootstrap()`。
2. 若 `saved_state is None`，直接返回。
3. 循环执行：
   - `time.sleep(MAIN_LOOP_POLL_INTERVAL_SEC)`
   - `saved_state = run_cycle(info, trader, saved_state)`
   - 若 `saved_state is None`，返回。

`main` 是固定 polling loop。

`main` 没有通用 exception shell；未捕获异常会终止调用栈。

## 10. Decision

`decide_cycle_action(orders, saved_state, current_btc_mid=None)` 只返回：

- `("keep", None)`
- `("rebuild", reference_price)`
- `("rebuild", None)`
- `("abnormal", None)`

每轮先调用 `classify_order_mode(orders)`，取得 `current_state`。

### `saved_state["mode"] == PAIR_MODE`

决策顺序：

1. 若当前 `current_state["mode"] == SELL_ONLY_MODE`，视为 BUY filled，返回 `("rebuild", saved_state["buy_price"])`。
2. 若当前 `current_state["mode"] == BUY_ONLY_MODE`，视为 SELL filled，返回 `("rebuild", saved_state["sell_price"])`。
3. 若当前 `current_state["mode"] == PAIR_MODE`，且 live `buy_price` 等于 saved `buy_price`，并且 live `sell_price` 等于 saved `sell_price`，返回 `("keep", None)`。
4. 否则返回 `("abnormal", None)`。

PAIR fill-driven rebuild 使用成交侧的 saved price 作为 `reference_price`，不读取 fresh BTC grid。

### `saved_state["mode"] == BUY_ONLY_MODE`

决策顺序：

1. 若 `orders` 为空，视为 residual BUY fill complete，返回 `("rebuild", saved_state["buy_price"])`。
2. 若 `current_state["mode"] == BUY_ONLY_MODE`，执行 anchor break 检查。
3. 若 anchor break 触发，返回 `("rebuild", None)`。
4. 若未触发 anchor break，返回 `("keep", None)`。
5. 否则返回 `("abnormal", None)`。

当前 `BUY_ONLY` keep 不校验 live BUY price 是否等于 `saved_state["buy_price"]`。

### `saved_state["mode"] == SELL_ONLY_MODE`

决策顺序：

1. 若 `orders` 为空，视为 residual SELL fill complete，返回 `("rebuild", saved_state["sell_price"])`。
2. 若 `current_state["mode"] == SELL_ONLY_MODE`，返回 `("keep", None)`。
3. 否则返回 `("abnormal", None)`。

当前 `SELL_ONLY` keep 不校验 live SELL price 是否等于 `saved_state["sell_price"]`。

### Anchor Break

anchor break 只存在于 `BUY_ONLY_MODE` 分支。

触发条件：

- `REANCHOR_BREAK` 为真
- `current_btc_mid` truthy 且 `current_btc_mid > 0`
- 当前 live BUY order 存在
- `price_distance_at_least(current_btc_mid, buy_order["price"], distance)` 为真

其中：

- `distance = (BUY_GRID_FACTOR + REANCHOR_BREAK_STEPS) * GRID_STEP`

触发结果：

- 返回 `("rebuild", None)`
- 后续 `rebuild` 使用 fresh `read_btc_grid(info)` 计算 `reference_price`

当前实现比较的是 `current_btc_mid` 与 live BUY order price，不直接比较 saved `reference_price`。

## 11. Rebuild

`rebuild(info, trader, orders, reference_price=None)` 当前选择路径：

1. 若 `is_fill_replace_path(orders, reference_price)` 为真，调用 `place_fill_replace_pair(info, trader, orders[0], reference_price)`。
2. 否则，如有 `orders`，先调用 `cleanup_orders(info, trader, orders)`。
3. 若 cleanup 返回假值，`rebuild` 返回 `None`。
4. 若 `reference_price is None`，调用 `read_btc_grid(info)`。
5. 调用 `place_pair(info, trader, reference_price)` 并返回结果。

### `is_fill_replace_path`

fill-replace path 的实际条件为：

- `len(orders) == 1`
- `reference_price is not None`
- `orders[0].get("oid") is not None`
- `orders[0].get("side") in {"BUY", "SELL"}`

在当前 `run_cycle` 调用路径中，该 path 对应 `PAIR` fill-driven rebuild 后只剩一张 live order 的情况。

## 12. Fill-Replace Path

`place_fill_replace_pair(info, trader, old_order, reference_price)` 当前流程：

1. 调用 `build_pair(reference_price)` 生成新 BUY 与新 SELL action。
2. 若 `old_order["side"] == "SELL"`，先 place 新 BUY，再 place 新 SELL。
3. 若 `old_order["side"] == "BUY"`，先 place 新 SELL，再 place 新 BUY。
4. 第一张新单必须 `ok`，否则直接返回 `None`。
5. 第一张新单成功后，调用 `cancel_order_by_oid(trader, old_order)`。
6. 调用 `wait_order_absent(info, old_order["oid"])` 验证旧 oid 已不存在。
7. cancel 或 verify 抛异常时，记录日志，调用 `cleanup_after_partial_place_failure`，返回 `None`。
8. 若旧 oid 仍存在，记录日志，调用 `cleanup_after_partial_place_failure`，返回 `None`。
9. 验证成功后 place 第二张新单。
10. 若第二张新单 `ok`，返回 `PAIR` rebuild state。
11. 若第二张新单为 SELL，且结果为 `insufficient_spot_balance`，且 `ALLOW_BUY_ONLY_WHEN_NO_BTC` 为真，返回 `BUY_ONLY` rebuild state。
12. 若第二张新单为 BUY，且结果为 `insufficient_spot_balance`，且 `ALLOW_SELL_ONLY_WHEN_NO_USDC` 为真，返回 `SELL_ONLY` rebuild state。
13. 其他情况调用 `cleanup_after_partial_place_failure` 并返回 `None`。

fill-replace verify 的目标是旧 oid absent，不要求 open orders 归零。

## 13. Cleanup

### `cleanup_orders`

`cleanup_orders(info, trader, orders)` 当前行为：

- `orders` 未传入时先读取当前 open orders。
- 使用 `trader.bulk_cancel([{"coin": SYMBOL, "oid": order["oid"]}, ...])` 一次提交全部撤单请求。
- bulk cancel 调用后，调用 `wait_no_open_orders(info)`。
- 若确认无 open orders，返回 `True`。
- 若仍有 open orders，记录 cleanup failure，返回 `False`。

当前限制：

- `cleanup_orders` 假设每个待 cancel order 都有 `oid`。
- 缺少 `oid` 时会因 `order["oid"]` 抛出 `KeyError`。

### `cleanup_after_partial_place_failure`

`cleanup_after_partial_place_failure(info, trader)` 当前行为：

- 调用 `read_orders(info)`。
- 若无 remaining orders，返回 `True`。
- 若有 remaining orders，输出摘要并调用 `cleanup_orders`。
- cleanup 成功返回 `True`。
- cleanup 失败记录 fatal 日志并返回 `False`。

调用方在 placement failure 后返回 `None`；该 cleanup 返回值不转换为成功 state。

## 14. Placement

### `build_pair`

`build_pair(reference_price)` 当前行为：

- `reference_price <= 0` 时抛出 `ValueError("Invalid reference price")`。
- `buy_price = reference_price - (BUY_GRID_FACTOR * GRID_STEP)`。
- `sell_price = reference_price + (SELL_GRID_FACTOR * GRID_STEP)`。
- `buy_price <= 0` 时抛出 `ValueError("Invalid buy price")`。
- BUY size 为 `round(BUDGET_USDC / buy_price, 5)`。
- SELL size 为 `round(BUDGET_USDC / sell_price, 5)`。

sizing invariant：

- 当前 sizing 是 same notional。
- BUY 与 SELL 使用各自价格分别按 `BUDGET_USDC` 反推 size。
- BUY size 与 SELL size 可以不同。
- size 不参与 `classify_order_mode` 的 pair validity 判定。

### `classify_order_result`

下单结果只分类为：

- `"ok"`
- `"insufficient_spot_balance"`
- `"error"`

规则：

- 缺少 statuses 返回 `"error"`。
- 第一条 status 含 `"error"` 时：
  - error 文本含 `"Insufficient spot balance"` 返回 `"insufficient_spot_balance"`。
  - 否则返回 `"error"`。
- 第一条 status 含 `"resting"` 或 `"filled"` 返回 `"ok"`。
- 其他情况返回 `"error"`。

### `place_limit_order`

`place_limit_order(trader, action)` 调用：

- `trader.order(...)`
- `SYMBOL`
- `action["side"] == "BUY"` 作为 is_buy
- `float(action["size"])`
- `float(action["price"])`
- `{"limit": {"tif": "Gtc"}}`
- `False`

返回 `classify_order_result(result)`。

### `place_pair`

`place_pair(info, trader, reference_price)` 当前流程：

1. 调用 `build_pair(reference_price)`。
2. 记录 rebuild 价格。
3. 先 place BUY。
4. 再 place SELL。
5. BUY 与 SELL 都 `ok` 时返回 `PAIR` rebuild state。
6. BUY `ok`、SELL `insufficient_spot_balance`、且 `ALLOW_BUY_ONLY_WHEN_NO_BTC` 为真时，返回 `BUY_ONLY` rebuild state。
7. SELL `ok`、BUY `insufficient_spot_balance`、且 `ALLOW_SELL_ONLY_WHEN_NO_USDC` 为真时，返回 `SELL_ONLY` rebuild state。
8. 其他情况调用 `cleanup_after_partial_place_failure` 并返回 `None`。

`place_pair` 的返回值只允许是：

- `PAIR` state
- `BUY_ONLY` state
- `SELL_ONLY` state
- `None`

## 15. Failure Semantics

当前实现的 failure 行为：

- `decide_cycle_action` 未命中 accepted path 时返回 `("abnormal", None)`。
- `run_cycle` 收到 abnormal action 后输出当前 orders 摘要，记录 `abnormal`，返回 `None`。
- `main` 收到 `saved_state is None` 后退出。
- `rebuild` 返回 `None` 时，`run_cycle` 记录 `rebuild failed` 并返回 `None`。
- `bootstrap` rebuild 返回 `None` 时，记录 `Bootstrap Rebuild Failed` 并返回 `None` state。

当前实现没有全局 exception shell。

以下异常不会被 `grid.py` 统一捕获：

- `read_orders` 抛出的异常
- `read_btc_mid` 抛出的异常
- `read_btc_grid` 抛出的异常
- `cleanup_orders` 中缺少 `oid` 导致的 `KeyError`
- `build_pair` 抛出的 `ValueError`
- 其他未被局部 try/except 捕获的 execution exception

`place_fill_replace_pair` 仅对 cancel/verify 阶段做局部 exception handling。

## 16. Current Invariants

当前实现满足以下不变量：

- `grid.py` 标准入口先应用 runtime overrides，再导入其他 grid 模块。
- execution client 变量名为 `trader`。
- `saved_state` 继续运行时只使用 `PAIR`、`BUY_ONLY`、`SELL_ONLY`。
- `ABNORMAL` 不作为 saved state 返回给主循环继续运行。
- `classify_order_mode` 始终返回 live classification state，并以 `mode` 表达 `PAIR` / `BUY_ONLY` / `SELL_ONLY` / `ABNORMAL`。
- 合法 `PAIR` 要求两张单、一买一卖、`buy_price < sell_price`、normalized gap 等于 `(BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP`。
- `BUY_ONLY` 与 `SELL_ONLY` 是单 order mode。
- `decide_cycle_action` 先分类 current mode，再按 saved mode 分支。
- `PAIR` fill-driven rebuild 优先于 `PAIR` keep。
- `BUY_ONLY` residual fill-complete 优先于 anchor break。
- `BUY_ONLY` anchor break 触发后以 `reference_price=None` rebuild。
- `run_cycle` 只在 saved mode 为 `BUY_ONLY` 时读取 BTC mid。
- `build_pair` 使用 same notional sizing。
- `place_pair` 先 place BUY，再 place SELL。
- general rebuild 在 placement 前 cleanup 现有 orders。
- general rebuild 只在 `reference_price is None` 时调用 `read_btc_grid`。
- fill-replace path 先 place 缺失替换侧，再 cancel 旧 oid，再 verify 旧 oid absent，再 place 另一侧。
- rebuild 失败后主循环不继续使用旧 `saved_state`。

## 17. Current Known Limitations

当前实现明确存在以下限制：

- `classify_order_mode` 不校验 order size。
- `BUY_ONLY` keep 不校验 live BUY price 与 saved BUY price 相等。
- `SELL_ONLY` keep 不校验 live SELL price 与 saved SELL price 相等。
- `BUY_ONLY` live price drift 在未触发 anchor break 时仍可能 keep。
- `cleanup_orders` 对缺少 `oid` 的 order 会抛出 `KeyError`。
- `grid.py` 没有全局 exception shell。
