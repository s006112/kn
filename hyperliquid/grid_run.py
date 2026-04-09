"""grid_run.py

职责
该模块运行 `UBTC/USDC` 的 Hyperliquid 现货网格流程：加载凭证，从当前挂单推导或恢复网格状态，在需要时重建成对或单边订单，并在出现连续异常状态或重建失败前持续循环。

流程:
- 启动 -> 凭证 -> 快照 -> 校验 -> 重建
- 循环 -> 订单 -> 分类 -> 决策 -> 重建
- 动作 -> 提交 -> 分类 -> 状态 -> 监控

不变式
- 模块在所有决策路径中都使用已配置的交易对、价格间距、容差和模式常量。
- 只有当 `PAIR`、`BUY_ONLY` 或 `SELL_ONLY` 模式下的下单成功时，重建才会返回可用状态；异常或失败的重建不会继续推进流程。
- 主循环在执行 `keep` 或成功 `rebuild` 后会重置异常计数，并在连续出现 `MAX_ABNORMAL_COUNT` 次异常结果后退出。

范围外
- 不定义导入的辅助函数之外的配对校验或残单重锚规则。
- 不在进程内存之外持久化状态。
- 不恢复交易所客户端创建、中间价读取或异常订单载荷触发的异常。
"""

import os
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_KEY = os.getenv("HYPERLIQUID_API_KEY")

SYMBOL = "UBTC/USDC"
BTC_MID_KEY = "@142"

BUDGET_USDC = 100.0
GRID_STEP = 100.0
BUY_GRID_FACTOR = 1.5
SELL_GRID_FACTOR = 1.5
PAIR_PRICE_TOLERANCE = 0.1
ALLOW_BUY_ONLY_WHEN_NO_BTC = True
ALLOW_SELL_ONLY_WHEN_NO_USDC = True
REANCHOR_BREAK = True
REANCHOR_BREAK_STEPS = 2
KEEP_LOG_INTERVAL_SEC = 300

PAIR_MODE = "PAIR"
BUY_ONLY_MODE = "BUY_ONLY"
SELL_ONLY_MODE = "SELL_ONLY"
ABNORMAL_MODE = "ABNORMAL"
MAX_ABNORMAL_COUNT = 3

last_keep_log_type = None
last_keep_log_ts = 0.0
GMT_PLUS_8 = timezone(timedelta(hours=8))

from grid_logic import (
    classify_order_shape,
    get_pair_state,
    pair_matches_state,
    should_reanchor_residual_order,
)

def get_open_orders(info):
    """作用:
    获取当前配置账户地址的未完成订单。
    """
    return info.open_orders(ACCOUNT_ADDRESS)


def log_msg(message):
    """作用:
    打印带时间戳的日志行。
    """
    timestamp = datetime.now(GMT_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def format_price(price): # 将价格格式化为整数显示字符串。
    return str(int(round(float(price))))


def log_keep_state(keep_type, message):
    """作用:
    按类型和时间间隔限制重复的 keep 状态日志输出。

    输入:
    keep_type: 用于抑制同类重复日志的标签。
    message: 通过限流检查后要输出的日志文本。

    输出:
    返回 `None`。仅当 `keep_type` 发生变化，或距离上次同类日志至少经过 `KEEP_LOG_INTERVAL_SEC` 秒时，才会更新模块级 keep 日志跟踪状态。
    """
    global last_keep_log_type
    global last_keep_log_ts

    now = time.time()
    if (
        keep_type != last_keep_log_type
        or now - last_keep_log_ts >= KEEP_LOG_INTERVAL_SEC
    ):
        log_msg(message)
        last_keep_log_type = keep_type
        last_keep_log_ts = now


def summarize_orders(orders):
    """作用:
    记录当前挂单摘要并统计买卖两侧数量。

    输入:
    orders: 可迭代的订单映射对象，包含 `side`、`limitPx`、`sz` 和 `oid` 字段。

    输出:
    返回 `(buy_count, sell_count)` 元组。输入为空时记录 `"OPEN ORDERS: none"`；否则记录以竖线分隔的订单摘要，其中 `side == "B"` 显示为 `BUY`，其余值显示为 `SELL`。
    """
    if not orders:
        log_msg("OPEN ORDERS: none")
        return 0, 0

    parts = []
    buy_count = 0
    sell_count = 0
    for order in orders:
        side = "BUY" if order["side"] == "B" else "SELL"
        parts.append(f"{side} = {format_price(order['limitPx'])}")   #  size={order['sz']} oid={order['oid']}
        if order["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    log_msg(f"OPEN ORDERS: {' | '.join(parts)}")
    return buy_count, sell_count


def get_mid_reference_price(info):
    """作用:
    根据当前 BTC 中间价和配置的网格步长推导参考价。

    输入:
    info: 带有 `all_mids` 方法的 Hyperliquid `Info` 客户端。

    输出:
    返回向下取整到最近 `GRID_STEP` 的 BTC 中间价浮点值。若中间价缺失或非正、取整后的参考价非正、或推导出的买价非正，则抛出 `ValueError`。透传 `info.all_mids()` 或浮点转换抛出的异常。
    """
    btc_mid = float(info.all_mids().get(BTC_MID_KEY, 0))
    if btc_mid <= 0:
        raise ValueError("Failed to get BTC mid price")

    reference_price = float(int((btc_mid / GRID_STEP) + 0.5) * GRID_STEP)   # round-half-up
    if reference_price <= 0:
        raise ValueError("Invalid reference price")
    if reference_price - (BUY_GRID_FACTOR * GRID_STEP) <= 0:
        raise ValueError("Invalid buy price")

    return reference_price


def build_pair(reference_price):
    """作用:
    围绕给定参考价构造配置好的买卖限价单动作。

    输入:
    reference_price: 正数价格锚点，用于推导买卖价格和共享下单数量。

    输出:
    返回 `(buy_action, sell_action)` 元组，元素均为包含 `side`、`price` 和 `size` 的映射，其中 `size` 为 `round(BUDGET_USDC / reference_price, 5)`。当 `reference_price` 非正或计算出的买价非正时抛出 `ValueError`。
    """
    if reference_price <= 0:
        raise ValueError("Invalid reference price")

    buy_price = reference_price - (BUY_GRID_FACTOR * GRID_STEP)
    sell_price = reference_price + (SELL_GRID_FACTOR * GRID_STEP)

    if buy_price <= 0:
        raise ValueError("Invalid buy price")

    size = round(BUDGET_USDC / reference_price, 5)
    return (
        {"side": "BUY", "price": buy_price, "size": size},
        {"side": "SELL", "price": sell_price, "size": size},
    )


def classify_order_result(result):
    """作用:
    将交易所下单响应归一化为模块内部使用的状态标签。

    输入:
    result: 可能包含 `response.data.statuses` 的映射对象。

    输出:
    当首个状态包含 `resting` 或 `filled` 时返回 `"ok"`；当首个状态包含带有 `Insufficient spot balance` 的错误信息时返回 `"insufficient_spot_balance"`；缺少状态或其余所有状态形态均返回 `"error"`。在返回非 `"ok"` 结果前会记录失败日志。
    """
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        log_msg(f"order failed: {result}")
        return "error"

    status = statuses[0]
    if "error" in status:
        error = status["error"]
        log_msg(f"order failed: {error}")
        if "Insufficient spot balance" in error:
            return "insufficient_spot_balance"
        return "error"

    if "resting" in status or "filled" in status:
        return "ok"

    log_msg(f"order failed: {result}")
    return "error"


def place_limit_order(exchange, action):
    """作用:
    提交一笔 GTC 限价单，并对交易所响应进行分类。

    输入:
    exchange: 带有 `order` 方法的 Hyperliquid `Exchange` 客户端。
    action: 包含 `side`、`size` 和 `price` 的映射，其中 `side == "BUY"` 时表示买单。

    输出:
    返回 `classify_order_result()` 归一化后的状态字符串。透传 `exchange.order()`、动作字段访问或类型转换抛出的异常。
    """
    result = exchange.order(
        SYMBOL,
        action["side"] == "BUY",
        float(action["size"]),
        float(action["price"]),
        {"limit": {"tif": "Gtc"}},
        False,
    )
    return classify_order_result(result)


def wait_no_open_orders(info, max_tries=10, interval_sec=1.0):
    """作用:
    轮询直到配置账户没有挂单，或达到重试上限。

    输入:
    info: 带有 `open_orders` 方法的 Hyperliquid `Info` 客户端。
    max_tries: 最大轮询次数。
    interval_sec: 每次轮询之间的睡眠秒数。

    输出:
    当某次轮询观察到没有挂单时返回 `True`；否则在耗尽 `max_tries` 后返回 `False`。透传 `get_open_orders()` 抛出的异常。
    """
    for _ in range(max_tries):
        if not get_open_orders(info):
            return True
        time.sleep(interval_sec)
    return False


def cleanup_orders(info, exchange, orders):
    """作用:
    撤销给定挂单并确认它们已经全部消失。

    输入:
    info: 用于确认订单是否已移除的 Hyperliquid `Info` 客户端。
    exchange: 带有 `bulk_cancel` 方法的 Hyperliquid `Exchange` 客户端。
    orders: 包含 `oid` 字段的订单映射可迭代对象。

    输出:
    当 `orders` 为空时立即返回 `True`；或在调用 `bulk_cancel()` 后通过 `wait_no_open_orders()` 确认成功时返回 `True`。若等待后仍有挂单，则记录 cleanup failure 日志并返回 `False`。透传 `bulk_cancel()`、订单字段访问或 `wait_no_open_orders()` 抛出的异常。
    """
    if not orders:
        return True

    exchange.bulk_cancel([{"coin": SYMBOL, "oid": order["oid"]} for order in orders])
    if wait_no_open_orders(info):
        return True

    log_msg("cleanup failure: open orders still remain")
    return False


def place_pair(exchange, reference_price):
    """作用:
    执行一次网格重建的下单动作（同时提交买单与卖单），并根据下单结果生成新的策略状态。

    输入:
    exchange: Hyperliquid 的 `Exchange` 客户端，用于提交订单。
    reference_price: 本次下单/重建使用的价格锚点，用于构造新的买卖挂单价格。

    输出:
    返回一个 `state` 字典，包含 `mode`、`buy_price`、`sell_price` 和 `reference_price`。
    其中 `reference_price` 表示本次构造新订单时使用的 placement / rebuild anchor；
    它不是 `get_pair_state()` 返回的 `pair_center_price`，两者字段名和语义都不相同。

    状态规则:
    若买卖订单均成功（`"ok"`），则 `mode = PAIR`；
    若仅买单成功且卖单因余额不足失败，则 `mode = BUY_ONLY`（需开启对应 fallback）；
    若仅卖单成功且买单因余额不足失败，则 `mode = SELL_ONLY`（需开启对应 fallback）；
    其他情况均视为 `ABNORMAL`。

    异常:
    不捕获 `build_pair()` 或 `place_limit_order()` 抛出的异常，调用方需自行处理。
    """
    buy_action, sell_action = build_pair(reference_price)
    log_msg(
        f"rebuilding: ref = {format_price(reference_price)} | "
        f"buy = {format_price(buy_action['price'])} | sell = {format_price(sell_action['price'])}"
    )
    buy_status = place_limit_order(exchange, buy_action)
    sell_status = place_limit_order(exchange, sell_action)

    if buy_status == "ok" and sell_status == "ok":
        return {
            "mode": PAIR_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    if (
        buy_status == "ok"
        and sell_status == "insufficient_spot_balance"
        and ALLOW_BUY_ONLY_WHEN_NO_BTC
    ):
        log_msg("buy-only fallback")
        return {
            "mode": BUY_ONLY_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    if (
        sell_status == "ok"
        and buy_status == "insufficient_spot_balance"
        and ALLOW_SELL_ONLY_WHEN_NO_USDC
    ):
        log_msg("sell-only fallback")
        return {
            "mode": SELL_ONLY_MODE,
            "buy_price": buy_action["price"],
            "sell_price": sell_action["price"],
            "reference_price": reference_price,
        }

    return {
        "mode": ABNORMAL_MODE,
        "buy_price": buy_action["price"],
        "sell_price": sell_action["price"],
        "reference_price": reference_price,
    }


def rebuild(info, exchange, orders, reference_price=None):
    """作用:
    撤销已有挂单，在需要时选择参考价，并尝试建立一个非异常状态。

    输入:
    info: 用于查询订单以及可选中间价读取的 Hyperliquid `Info` 客户端。
    exchange: 用于撤单和下单的 Hyperliquid `Exchange` 客户端。
    orders: 重建前需要撤销的当前挂单。
    reference_price: 可选的显式参考价；当其为 `None` 时使用 `get_mid_reference_price()`。

    输出:
    当 `place_pair()` 返回的状态不是 `ABNORMAL` 时，返回该状态；该返回值沿用
    `place_pair()` 的 schema，因此包含 `reference_price` 这一重建锚点字段，而不会改写成
    `get_pair_state()` 使用的 `pair_center_price`。若前置或后置清理失败，或下单结果为
    `ABNORMAL`，则返回 `None`。透传清理、参考价推导、订单查询或下单过程抛出的异常。
    """
    if orders and not cleanup_orders(info, exchange, orders):
        return None

    if reference_price is None:
        reference_price = get_mid_reference_price(info)

    state = place_pair(exchange, reference_price)
    if state is not None and state["mode"] != ABNORMAL_MODE:
        return state

    orders = get_open_orders(info)
    if orders and not cleanup_orders(info, exchange, orders):
        return None

    return None


def resolve_fill_rebuild_action(state, orders, order_shape):
    """作用:
    判断是否发生了成交驱动的重建条件，并返回对应动作。

    输入:
    state: 至少包含 `mode`、`buy_price` 和 `sell_price` 的状态映射。该状态可能来自
    `get_pair_state()`（包含 `pair_center_price`）或 `place_pair()`（包含 `reference_price`），
    但本函数不要求这两个字段存在，也不将它们视为同一概念。
    orders: 当前挂单集合，用于检查残单是否已经完成。
    order_shape: `orders` 的当前形态分类结果。

    输出:
    当先前状态为 `PAIR` 且当前变为单边残单时，返回 `("rebuild", reference_price)`，并使用已成交那一侧此前的价格作为重建参考价。对于 `BUY_ONLY` 或 `SELL_ONLY` 状态，当当前已无挂单时，也返回 `("rebuild", reference_price)`，并使用该残单侧保存的价格。其余情况返回 `None`。
    """
    previous_mode = state["mode"]

    if previous_mode == PAIR_MODE:
        if order_shape == SELL_ONLY_MODE:
            log_msg("🔥 fill detected: BUY filled -> SELL residual -> rebuild")
            return "rebuild", state["buy_price"]

        if order_shape == BUY_ONLY_MODE:
            log_msg("✅ fill detected: SELL filled -> BUY residual -> rebuild")
            return "rebuild", state["sell_price"]

        return None

    if previous_mode == BUY_ONLY_MODE and len(orders) == 0:
        log_msg("🔥 residual fill completed: BUY_ONLY -> rebuild")
        return "rebuild", state["buy_price"]

    if previous_mode == SELL_ONLY_MODE and len(orders) == 0:
        log_msg("✅ residual fill completed: SELL_ONLY -> rebuild")
        return "rebuild", state["sell_price"]

    return None


def validate_keep_state(info, orders, state, order_shape):
    """作用:
    判断当前订单是否仍满足成对状态的 keep 条件。

    输入:
    info: 调用链保留但当前未使用的参数。
    orders: 当前挂单集合。
    state: 至少包含 `mode`、`buy_price` 和 `sell_price` 的已保存状态映射。该状态可能来自
    `get_pair_state()` 并带有 `pair_center_price`，也可能来自 `place_pair()` 并带有
    `reference_price`；本函数只校验保存的买卖价格，不假定这两个字段等价。
    order_shape: `orders` 的当前形态分类结果。

    输出:
    仅在 `PAIR` 状态下可能返回 `True`。仅当 `order_shape` 为 `PAIR`、`get_pair_state()` 成功且 `pair_matches_state()` 确认两侧价格均在容差内时返回 `True`。其余路径均返回 `False`。透传配对校验辅助函数因异常订单字段抛出的异常。
    """
    if state["mode"] == PAIR_MODE:
        if order_shape != PAIR_MODE:
            return False

        current_pair = get_pair_state(
            orders,
            GRID_STEP,
            BUY_GRID_FACTOR,
            SELL_GRID_FACTOR,
            PAIR_PRICE_TOLERANCE,
            PAIR_MODE,
        )
        if current_pair is None:
            return False
        if not pair_matches_state(current_pair, state, PAIR_PRICE_TOLERANCE):
            return False
        log_keep_state("pair", "contract: keep pair")
        return True

    return False


def detect_anchor_break(info, orders, state, order_shape):
    """作用:
    检测单边残单是否已经偏离 BTC 中间价到必须重建的程度。

    输入:
    info: 被残单重锚辅助函数使用的 Hyperliquid `Info` 客户端。
    orders: 当前挂单集合。
    state: 至少包含 `mode` 的已保存状态映射。该状态可以带有 `pair_center_price` 或
    `reference_price`，但本函数只根据 `mode` 和当前订单检查单边重锚，不把两者混为同一字段。
    order_shape: `orders` 的当前形态分类结果。

    输出:
    仅当重锚功能开启、保存的模式为单边、当前形态与该模式一致、当前恰好只有一笔订单且 `should_reanchor_residual_order()` 返回 `True` 时，返回 `("rebuild", None)`。其余路径返回 `None`。在辅助函数完成自身保护性检查之后，透传其因异常订单字段抛出的异常。
    """
    if not REANCHOR_BREAK:
        return None

    if state["mode"] not in (BUY_ONLY_MODE, SELL_ONLY_MODE):
        return None

    if order_shape != state["mode"]:
        return None

    if len(orders) != 1:
        return None

    if not should_reanchor_residual_order(
        info,
        orders,
        state["mode"],
        BUY_ONLY_MODE,
        SELL_ONLY_MODE,
        BTC_MID_KEY,
        GRID_STEP,
        REANCHOR_BREAK,
        REANCHOR_BREAK_STEPS,
    ):
        return None

    log_msg("🪝 contract: anchor break")
    return "rebuild", None


def detect_single_sided_action(info, orders, state, order_shape):
    """作用:
    决定单边状态应继续保持还是触发重建。

    输入:
    info: 用于评估 anchor break 的 Hyperliquid `Info` 客户端。
    orders: 当前挂单集合。
    state: 至少包含 `mode` 的已保存状态映射，可能来自 `get_pair_state()` 的
    `pair_center_price` schema，也可能来自 `place_pair()` 的 `reference_price` schema；
    本函数不要求也不推断这两个字段等价。
    order_shape: `orders` 的当前形态分类结果。

    输出:
    对于未触发 anchor break 的有效单边残单，返回 `("keep", None)`。当 `detect_anchor_break()` 请求重建时，返回其结果。当保存模式不是单边、当前形态与保存模式不一致，或残单方向与预期方向不一致时，返回 `None`。
    """
    if state["mode"] == BUY_ONLY_MODE:
        if order_shape != BUY_ONLY_MODE:
            return None
        if len(orders) != 1 or orders[0]["side"] != "B":
            return None

        anchor_break_action = detect_anchor_break(info, orders, state, order_shape)
        if anchor_break_action is not None:
            return anchor_break_action

        log_keep_state("buy-only", "contract: keep buy-only")
        return "keep", None

    if state["mode"] == SELL_ONLY_MODE:
        if order_shape != SELL_ONLY_MODE:
            return None
        if len(orders) != 1 or orders[0]["side"] == "B":
            return None

        anchor_break_action = detect_anchor_break(info, orders, state, order_shape)
        if anchor_break_action is not None:
            return anchor_break_action

        log_keep_state("sell-only", "contract: keep sell-only")
        return "keep", None

    return None


def get_loop_action(info, orders, state):
    """
    作用:
    根据当前 open orders 与上一轮已接受的 state，
    判定这一轮主循环应执行的唯一动作。

    输入:
    info: 用于订单状态判断和潜在重建决策的 Hyperliquid `Info` 客户端。
    orders: 当前挂单集合。
    state: 上一轮已接受的状态映射，可能来自 `get_pair_state()` 并包含
    `pair_center_price`，也可能来自 `place_pair()` 并包含 `reference_price`；
    本函数及其调用链不会将这两个字段名或语义统一。

    判定顺序:
    1. 先分类当前订单形状
    2. 优先检查是否命中 fill-driven rebuild
    3. 再检查 PAIR_MODE 是否满足 keep 条件
    4. 再检查 BUY_ONLY / SELL_ONLY 是否应 keep 或触发 anchor break
    5. 若以上都不满足，则判定为 abnormal

    返回:
    - ("keep", None)
    - ("rebuild", reference_price 或 None)
    - ("abnormal", None)

    边界:
    本函数只负责决策，不负责下单、撤单或实际 rebuild 执行。
    """
    order_shape = classify_order_shape(
        orders,
        GRID_STEP,
        BUY_GRID_FACTOR,
        SELL_GRID_FACTOR,
        PAIR_PRICE_TOLERANCE,
        PAIR_MODE,
        BUY_ONLY_MODE,
        SELL_ONLY_MODE,
        ABNORMAL_MODE,
    )

    fill_driven_action = resolve_fill_rebuild_action(state, orders, order_shape)
    if fill_driven_action is not None:
        return fill_driven_action

    if validate_keep_state(info, orders, state, order_shape):
        return "keep", None

    single_sided_action = detect_single_sided_action(info, orders, state, order_shape)
    if single_sided_action is not None:
        return single_sided_action

    log_msg("contract: abnormal")
    return "abnormal", None


def run_main_loop(info, exchange, state):
    """作用:
    持续轮询挂单，并在退出前执行 keep、rebuild 或 abnormal 处理。

    输入:
    info: 用于轮询与重建决策的 Hyperliquid `Info` 客户端。
    exchange: 需要执行重建时使用的 Hyperliquid `Exchange` 客户端。
    state: 运行中合约的初始已接受状态映射；若来自 `get_pair_state()`，可包含
    `pair_center_price`，若来自 `place_pair()`/`rebuild()`，则包含 `reference_price`。
    这些字段表示不同语义，本循环不会把它们视为同一概念。

    输出:
    返回 `None`。当动作持续解析为 `keep` 或成功的 `rebuild` 时无限循环；当重建失败、重建结果异常，或连续出现 `MAX_ABNORMAL_COUNT` 次异常循环动作时退出。过程中会记录异常摘要与退出条件。
    """
    abnormal_count = 0

    while True:
        time.sleep(1.0)
        orders = get_open_orders(info)
        action, reference_price = get_loop_action(info, orders, state)

        if action == "keep":
            abnormal_count = 0
            continue

        if action == "rebuild":
            state = rebuild(info, exchange, orders, reference_price)
            if state is None or state["mode"] == ABNORMAL_MODE:
                log_msg("rebuild failed or abnormal mode, exit")
                return
            abnormal_count = 0
            continue

        abnormal_count += 1
        buy_count, sell_count = summarize_orders(orders)
        log_msg(
            f"abnormal state {abnormal_count}/{MAX_ABNORMAL_COUNT}: "
            f"buy={buy_count} sell={sell_count}"
        )
        if abnormal_count >= MAX_ABNORMAL_COUNT:
            log_msg("abnormal exit")
            return


def create_exchange():
    """作用:
    为当前配置的 API key 和账户地址构造 Hyperliquid 交易客户端。

    输入:
    无。

    输出:
    返回绑定到 `constants.MAINNET_API_URL` 和 `ACCOUNT_ADDRESS` 的 `Exchange` 实例。当 `API_KEY` 缺失时抛出 `ValueError`。透传 `Account.from_key()` 或 `Exchange` 抛出的异常。
    """
    if not API_KEY:
        raise ValueError("Missing HYPERLIQUID_API_KEY")

    return Exchange(
        Account.from_key(API_KEY),
        constants.MAINNET_API_URL,
        account_address=ACCOUNT_ADDRESS,
    )


def main():
    """作用:
    从当前挂单启动网格流程，并持续运行直到满足退出条件。

    输入:
    无。

    输出:
    返回 `None`。当 `ACCOUNT_ADDRESS` 缺失时立即记录日志并退出。否则创建客户端、
    汇总当前挂单、在存在有效配对时直接接受 `get_pair_state()` 返回的状态
    （其中包含 `pair_center_price` 这一配对快照中点），必要时回退到 `rebuild()`
    获取包含 `reference_price` 的下单锚点状态，并仅在建立了非异常初始状态后进入
    `run_main_loop()`。透传客户端创建、状态推导和循环执行过程抛出的异常。
    """
    if not ACCOUNT_ADDRESS:
        log_msg("Missing HYPERLIQUID_ACCOUNT_ADDRESS")
        return

    info = Info(constants.MAINNET_API_URL)
    exchange = create_exchange()

    orders = get_open_orders(info)
    buy_count, sell_count = summarize_orders(orders)

    state = None
    if buy_count == 1 and sell_count == 1:
        state = get_pair_state(
            orders,
            GRID_STEP,
            BUY_GRID_FACTOR,
            SELL_GRID_FACTOR,
            PAIR_PRICE_TOLERANCE,
            PAIR_MODE,
        )
        if state is not None:
            log_msg("pair valid")

    if state is None:
        state = rebuild(info, exchange, orders)
        if state is None or state["mode"] == ABNORMAL_MODE:
            log_msg("initial rebuild failed or abnormal mode")
            return

    run_main_loop(info, exchange, state)


if __name__ == "__main__":
    main()
