"""Shared runtime config and lightweight logging helpers for the grid engine."""

import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_WALLET_KEY = os.getenv("HYPERLIQUID_API_WALLET_KEY")

SYMBOL = "UBTC/USDC"
BTC_MID_KEY = "@142"

BUDGET_USDC = 200.0
GRID_STEP = 200.0
BUY_GRID_FACTOR = 1.00
SELL_GRID_FACTOR = 1.00
ALLOW_BUY_ONLY_WHEN_NO_BTC = True
ALLOW_SELL_ONLY_WHEN_NO_USDC = True
REANCHOR_BREAK = True
REANCHOR_BREAK_STEPS = 2
KEEP_LOG_INTERVAL_SEC = 300
MAIN_LOOP_POLL_INTERVAL_SEC = 1.5
WAIT_NO_OPEN_ORDERS_INTERVAL_SEC = 1.5
OPEN_ORDERS_MAX_RETRIES = 4
OPEN_ORDERS_RETRY_BASE_SEC = 0.5
OPEN_ORDERS_RETRY_MAX_SEC = 4.0
OPEN_ORDERS_RETRY_JITTER_MAX_SEC = 0.3
OPEN_ORDERS_RETRY_COOLDOWN_SEC = 0.2
OPEN_ORDERS_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
BTC_MID_MAX_RETRIES = 2
BTC_MID_RETRY_BASE_SEC = 0.3
BTC_MID_RETRY_MAX_SEC = 0.6

PAIR_MODE = "PAIR"
BUY_ONLY_MODE = "BUY_ONLY"
SELL_ONLY_MODE = "SELL_ONLY"
ABNORMAL_MODE = "ABNORMAL"

last_keep_log_type = None
last_keep_log_ts = 0.0
GMT_PLUS_8 = timezone(timedelta(hours=8))


def log_msg(message):
    """作用:
    打印带时间戳的日志行。
    """
    timestamp = datetime.now(GMT_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def format_price(price):  # 将价格格式化为整数显示字符串。
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
        parts.append(f"{side} - {format_price(order['limitPx'])}")  #  size={order['sz']} oid={order['oid']}
        if order["side"] == "B":
            buy_count += 1
        else:
            sell_count += 1

    log_msg(f"OPEN ORDERS | {' | '.join(parts)}")
    return buy_count, sell_count
