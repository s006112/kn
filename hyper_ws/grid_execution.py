"""Order execution, cleanup, and rebuild helpers for the grid engine."""

import time

from grid_gateway import get_mid_reference_price, get_open_orders
from grid_config import (
    ABNORMAL_MODE,
    ALLOW_BUY_ONLY_WHEN_NO_BTC,
    ALLOW_SELL_ONLY_WHEN_NO_USDC,
    BUDGET_USDC,
    BUY_GRID_FACTOR,
    BUY_ONLY_MODE,
    GRID_STEP,
    PAIR_MODE,
    SELL_GRID_FACTOR,
    SELL_ONLY_MODE,
    SYMBOL,
    WAIT_NO_OPEN_ORDERS_INTERVAL_SEC,
    format_price,
    log_msg,
    summarize_orders,
)

def build_pair(reference_price):
    """作用:
    围绕给定参考价构造配置好的买卖限价单动作。

    输入:
    reference_price: 正数价格锚点，用于推导买卖价格和共享下单数量。

    输出:
    返回 `(buy_action, sell_action)` 元组，元素均为包含 `side`、`price` 和 `size` 的映射。
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


def wait_no_open_orders(
    info,
    max_tries=10,
    interval_sec=WAIT_NO_OPEN_ORDERS_INTERVAL_SEC,
):
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

    remaining_orders = get_open_orders(info)
    summarize_orders(remaining_orders)
    log_msg("cleanup failure: open orders still remain")
    return False

def try_cleanup_after_place_failure(info, exchange):
    try:
        orders = get_open_orders(info)
    except Exception as exc:
        log_msg(
            f"cleanup attempt failed while reading open orders: "
            f"{type(exc).__name__}: {exc}"
        )
        return

    if not orders:
        return

    try:
        cleanup_orders(info, exchange, orders)
    except Exception as exc:
        log_msg(f"cleanup attempt raised: {type(exc).__name__}: {exc}")


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
        f"REBUILD | SELL - {format_price(sell_action['price'])} | REF - {format_price(reference_price)} | BUY - {format_price(buy_action['price'])}"
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
    
    异常:
    若 `place_pair()` 过程中抛出异常，函数会先 best-effort 读取当前 open orders，
    并在发现残单时尝试 cleanup，然后再继续向上抛出原异常。    
    
    """
    if orders and not cleanup_orders(info, exchange, orders):
        return None

    if reference_price is None:
        reference_price = get_mid_reference_price(info)

    try:
        state = place_pair(exchange, reference_price)
    except Exception:
        try_cleanup_after_place_failure(info, exchange)
        raise

    if state is not None and state["mode"] != ABNORMAL_MODE:
        return state

    orders = get_open_orders(info)
    if orders and not cleanup_orders(info, exchange, orders):
        return None

    return None
