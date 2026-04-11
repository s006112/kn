"""grid.py

职责
该模块运行 `UBTC/USDC` 的 Hyperliquid 现货网格流程：加载凭证，从当前挂单推导或恢复网格状态，在需要时重建成对或单边订单，并在出现异常状态或重建失败时退出。

流程:
- 启动 -> 凭证 -> 快照 -> 校验 -> 重建
- 循环 -> 订单 -> strategy 判定 -> 重建
- 动作 -> 提交 -> 分类 -> 状态 -> 监控

不变式
- 模块在所有决策路径中都使用已配置的交易对、价格间距和模式常量。
- 只有当 `PAIR`、`BUY_ONLY` 或 `SELL_ONLY` 模式下的下单成功时，重建才会返回可用状态；异常或失败的重建不会继续推进流程。
- 主循环在执行 `keep` 或成功 `rebuild` 后继续运行，并在出现异常结果时立即退出。

范围外
- 不定义导入的策略模块和辅助函数之外的配对校验或残单重锚规则。
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

from grid_logic import classify_order_shape, get_pair_state
from grid_strategy_dispatch import get_loop_action

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_KEY = os.getenv("HYPERLIQUID_API_KEY")

SYMBOL = "UBTC/USDC"
BTC_MID_KEY = "@142"

BUDGET_USDC = 200.0
GRID_STEP = 100.0
BUY_GRID_FACTOR = 1.0
SELL_GRID_FACTOR = 1.25
ALLOW_BUY_ONLY_WHEN_NO_BTC = True
ALLOW_SELL_ONLY_WHEN_NO_USDC = True
REANCHOR_BREAK = True
REANCHOR_BREAK_STEPS = 2
KEEP_LOG_INTERVAL_SEC = 300

PAIR_MODE = "PAIR"
BUY_ONLY_MODE = "BUY_ONLY"
SELL_ONLY_MODE = "SELL_ONLY"
ABNORMAL_MODE = "ABNORMAL"
STRATEGY_KIND = "pair"

last_keep_log_type = None
last_keep_log_ts = 0.0
GMT_PLUS_8 = timezone(timedelta(hours=8))


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


def format_price(price):
    """作用:
    将价格格式化为整数显示字符串。
    """
    return str(int(round(float(price))))


def log_keep_state(keep_type, message):
    """作用:
    按类型和时间间隔限制重复的 keep 状态日志输出。

    输入:
    keep_type: 用于抑制同类重复日志的标签。
    message: 通过限流检查后要输出的日志文本。

    输出:
    返回 `None`。仅当 `keep_type` 发生变化，或距离上次同类日志至少经过
    `KEEP_LOG_INTERVAL_SEC` 秒时，才会更新模块级 keep 日志跟踪状态。
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
    返回 `(buy_count, sell_count)` 元组。输入为空时记录 `"OPEN ORDERS: none"`；
    否则记录以竖线分隔的订单摘要，其中 `side == "B"` 显示为 `BUY`，
    其余值显示为 `SELL`。
    """
    if not orders:
        log_msg("OPEN ORDERS: none")
        return 0, 0

    parts = []
    buy_count = 0
    sell_count = 0
    for order in orders:
        side = "BUY" if order["side"] == "B" else "SELL"
        parts.append(f"{side} - {format_price(order['limitPx'])}")
        if order["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    log_msg(f"OPEN ORDERS | {' | '.join(parts)}")
    return buy_count, sell_count


def get_mid_reference_price(info):
    """作用:
    根据当前 BTC 中间价和配置的网格步长推导参考价（对齐到最近网格）。

    输入:
    info: 带有 `all_mids` 方法的 Hyperliquid `Info` 客户端。

    输出:
    返回按 round-half-up 规则对齐到最近 `GRID_STEP` 的 BTC 中间价浮点值。
    即将 `btc_mid / GRID_STEP` 四舍五入（.5 一律进位）后再乘回 `GRID_STEP`，
    用于作为网格下单的参考锚点价格。

    行为说明:
    - 当价格位于两个网格中点以下时，向下对齐
    - 当价格位于中点及以上时，向上对齐（.5 → 上）
    - 不使用 Python 默认的 banker’s rounding（round-half-to-even）

    异常:
    - 当中间价缺失或非正时，抛出 `ValueError`
    - 当推导出的参考价非正时，抛出 `ValueError`
    - 当根据参考价计算出的买价非正时，抛出 `ValueError`
    - 透传 `info.all_mids()` 或浮点转换抛出的异常
    """
    btc_mid = float(info.all_mids().get(BTC_MID_KEY, 0))
    if btc_mid <= 0:
        raise ValueError("Failed to get BTC mid price")

    reference_price = float(int((btc_mid / GRID_STEP) + 0.5) * GRID_STEP)
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
    返回 `(buy_action, sell_action)` 元组，元素均为包含 `side`、`price` 和
    `size` 的映射。
    其中 `buy_action["size"] = round(BUDGET_USDC / buy_price, 5)`，
    `sell_action["size"] = round(BUDGET_USDC / sell_price, 5)`。
    当 `reference_price` 非正或计算出的买价非正时抛出 `ValueError`。
    """
    if reference_price <= 0:
        raise ValueError("Invalid reference price")

    buy_price = reference_price - (BUY_GRID_FACTOR * GRID_STEP)
    sell_price = reference_price + (SELL_GRID_FACTOR * GRID_STEP)

    if buy_price <= 0:
        raise ValueError("Invalid buy price")

    buy_size = round(BUDGET_USDC / buy_price, 5)
    sell_size = round(BUDGET_USDC / sell_price, 5)
    return (
        {"side": "BUY", "price": buy_price, "size": buy_size},
        {"side": "SELL", "price": sell_price, "size": sell_size},
    )


def classify_order_result(result):
    """作用:
    将交易所下单响应归一化为模块内部使用的状态标签。

    输入:
    result: 可能包含 `response.data.statuses` 的映射对象。

    输出:
    当首个状态包含 `resting` 或 `filled` 时返回 `"ok"`；
    当首个状态包含带有 `Insufficient spot balance` 的错误信息时返回
    `"insufficient_spot_balance"`；缺少状态或其余所有状态形态均返回 `"error"`。
    在返回非 `"ok"` 结果前会记录失败日志。
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
    返回 `classify_order_result()` 归一化后的状态字符串。
    透传 `exchange.order()`、动作字段访问或类型转换抛出的异常。
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
    当某次轮询观察到没有挂单时返回 `True`；否则在耗尽 `max_tries` 后返回 `False`。
    透传 `get_open_orders()` 抛出的异常。
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
    当 `orders` 为空时立即返回 `True`；或在调用 `bulk_cancel()` 后通过
    `wait_no_open_orders()` 确认成功时返回 `True`。
    若等待后仍有挂单，则记录 cleanup failure 日志并返回 `False`。
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
    返回一个 `state` 字典，包含 `mode`、`buy_price`、`sell_price` 和
    `reference_price`。
    其中 `reference_price` 表示本次构造新订单时使用的 placement / rebuild anchor；
    它不是 `get_pair_state()` 返回的 `pair_center_price`，两者字段名和语义都不相同。

    状态规则:
    若买卖订单均成功（`"ok"`），则 `mode = PAIR`；
    若仅买单成功且卖单因余额不足失败，则 `mode = BUY_ONLY`
    （需开启对应 fallback）；
    若仅卖单成功且买单因余额不足失败，则 `mode = SELL_ONLY`
    （需开启对应 fallback）；
    其他情况均视为 `ABNORMAL`。
    """
    buy_action, sell_action = build_pair(reference_price)
    log_msg(
        f"REBUILD | SELL - {format_price(sell_action['price'])} | "
        f"REF - {format_price(reference_price)} | "
        f"BUY - {format_price(buy_action['price'])}"
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
    清理当前挂单，并按给定或 fresh reference price 重建一组新订单。

    输入:
    info: 用于读取当前挂单和推导 mid reference 的 Hyperliquid `Info` 客户端。
    exchange: 用于撤单和下单的 Hyperliquid `Exchange` 客户端。
    orders: 当前 live open orders。
    reference_price: 可选的显式重建锚点；为 `None` 时会调用
    `get_mid_reference_price(info)` 计算 fresh reference。

    输出:
    当 cleanup 成功且 `place_pair()` 返回非 `ABNORMAL` 模式时，返回新的状态映射；
    否则返回 `None`。
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


def run_main_loop(info, exchange, state):
    """作用:
    持续轮询挂单，并在退出前执行 keep、rebuild 或 abnormal 处理。

    输入:
    info: 用于轮询与重建决策的 Hyperliquid `Info` 客户端。
    exchange: 需要执行重建时使用的 Hyperliquid `Exchange` 客户端。
    state: 运行中合约的初始已接受状态映射；若来自 `get_pair_state()`，
    可包含 `pair_center_price`，若来自 `place_pair()`/`rebuild()`，
    则包含 `reference_price`。这些字段表示不同语义，本循环不会把它们视为同一概念。

    输出:
    返回 `None`。当动作持续解析为 `keep` 或成功的 `rebuild` 时无限循环；
    当重建失败、重建结果异常，或动作解析为 abnormal 时立即退出。
    """
    while True:
        time.sleep(1.0)
        orders = get_open_orders(info)

        action, reference_price = get_loop_action(
            info,
            orders,
            state,
            strategy_kind=STRATEGY_KIND,
            grid_step=GRID_STEP,
            buy_grid_factor=BUY_GRID_FACTOR,
            sell_grid_factor=SELL_GRID_FACTOR,
            pair_mode=PAIR_MODE,
            buy_only_mode=BUY_ONLY_MODE,
            sell_only_mode=SELL_ONLY_MODE,
            abnormal_mode=ABNORMAL_MODE,
            reanchor_break=REANCHOR_BREAK,
            btc_mid_key=BTC_MID_KEY,
            reanchor_break_steps=REANCHOR_BREAK_STEPS,
            log_msg=log_msg,
            log_keep_state=log_keep_state,
            format_price=format_price,
        )

        if action == "keep":
            continue

        if action == "rebuild":
            state = rebuild(info, exchange, orders, reference_price)
            if state is None or state["mode"] == ABNORMAL_MODE:
                log_msg("rebuild failed or abnormal mode, exit")
                return
            continue

        buy_count, sell_count = summarize_orders(orders)
        log_msg(f"abnormal state: buy={buy_count} sell={sell_count}")
        log_msg("abnormal exit")
        return


def create_exchange():
    """作用:
    为当前配置的 API key 和账户地址构造 Hyperliquid 交易客户端。

    输入:
    无。

    输出:
    返回绑定到 `constants.MAINNET_API_URL` 和 `ACCOUNT_ADDRESS` 的 `Exchange` 实例。
    当 `API_KEY` 缺失时抛出 `ValueError`。
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
    返回 `None`。当 `ACCOUNT_ADDRESS` 缺失时立即记录日志并退出。
    否则创建客户端、拉取并记录当前挂单摘要，尝试把当前 snapshot 接受为初始
    `PAIR` state；若不能接受，则回退到 `rebuild()` 获取新的非异常状态，
    并仅在建立了有效初始状态后进入 `run_main_loop()`。
    """
    if not ACCOUNT_ADDRESS:
        log_msg("Missing HYPERLIQUID_ACCOUNT_ADDRESS")
        return

    info = Info(constants.MAINNET_API_URL)
    exchange = create_exchange()

    orders = get_open_orders(info)
    summarize_orders(orders)

    state = None
    order_shape = classify_order_shape(
        orders,
        GRID_STEP,
        BUY_GRID_FACTOR,
        SELL_GRID_FACTOR,
        PAIR_MODE,
        BUY_ONLY_MODE,
        SELL_ONLY_MODE,
        ABNORMAL_MODE,
    )

    if order_shape == PAIR_MODE:
        state = get_pair_state(
            orders,
            GRID_STEP,
            BUY_GRID_FACTOR,
            SELL_GRID_FACTOR,
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
