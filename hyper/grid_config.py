"""Shared config, price helpers, and logging for the grid engine."""

import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()


# ============================================================================
# env / account config
# ============================================================================

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_KEY = os.getenv("HYPERLIQUID_API_KEY")

SYMBOL = "UBTC/USDC"
BTC_MID_KEY = "@142"

# ============================================================================
# strategy params
# ============================================================================

BUDGET_USDC = 200.0  # 每侧订单按 100 USDC 名义金额下单，用它反推买卖数量
GRID_STEP = 200.0  # 参考价每上下偏移 100 美元挂一格，是网格的基础间距
BUY_GRID_FACTOR = 1.00  # 买单距离 = 1.00 x GRID_STEP，1.00 表示向下 1 格挂买单
SELL_GRID_FACTOR = 1.00  # 卖单距离 = 1.00 x GRID_STEP，1.00 表示向上 1 格挂卖单

ALLOW_BUY_ONLY_WHEN_NO_BTC = True  # 卖单因 BTC 不足下不出时，允许退化成仅挂买单
ALLOW_SELL_ONLY_WHEN_NO_USDC = True  # 买单因 USDC 不足下不出时，允许退化成仅挂卖单

REANCHOR_BREAK = True  # 启用 BUY_ONLY 模式下的重锚检测，偏离太远时整组重建
REANCHOR_BREAK_STEPS = 2  # 与 BTC 中间价相差达到 2 格时，触发 anchor break 重挂

def apply_runtime_overrides(overrides: dict):
    globals_ = globals()
    for k, v in overrides.items():
        if k in globals_:
            globals_[k] = v

# ============================================================================
# timing / retry
# ============================================================================

KEEP_LOG_INTERVAL_SEC = 300  # keep 状态同类日志至少每 300 秒打印一次，避免刷屏
MAIN_LOOP_POLL_INTERVAL_SEC = 1.5  # 主循环每 1.5 秒跑一轮，检查挂单和状态是否变化
WAIT_NO_OPEN_ORDERS_INTERVAL_SEC = 1.5  # 撤单后每隔 1.5 秒检查一次是否已无遗留挂单

OPEN_ORDERS_MAX_RETRIES = 4  # 查询挂单列表最多重试 4 次，容忍短暂接口抖动
OPEN_ORDERS_RETRY_BASE_SEC = 0.5  # 查询挂单重试的基础退避时间，从 0.5 秒开始
OPEN_ORDERS_RETRY_MAX_SEC = 4.0  # 查询挂单单次退避最长不超过 4 秒
OPEN_ORDERS_RETRY_JITTER_MAX_SEC = 0.3  # 给查询挂单重试附加最多 0.3 秒随机抖动，减少撞请求
OPEN_ORDERS_RETRY_COOLDOWN_SEC = 0.2  # 一轮查询挂单结束后补一个短冷却，避免立刻再次打接口
OPEN_ORDERS_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}  # 这些状态码视为临时故障，可安全重试

BTC_MID_MAX_RETRIES = 2  # 拉取 BTC 中间价最多重试 2 次，失败就放弃本轮 fresh anchor
BTC_MID_RETRY_BASE_SEC = 0.3  # BTC 中间价重试的基础等待时间
BTC_MID_RETRY_MAX_SEC = 0.6  # BTC 中间价单次重试等待上限，保持重建响应够快


# ============================================================================
# state / mode constants
# ============================================================================

PAIR_MODE = "PAIR"  # 正常双边模式，同时有 1 买 1 卖且价差符合网格规则
BUY_ONLY_MODE = "BUY_ONLY"  # 仅剩买单的残边模式，常见于卖侧因余额不足未成功挂出
SELL_ONLY_MODE = "SELL_ONLY"  # 仅剩卖单的残边模式，常见于买侧因余额不足未成功挂出
ABNORMAL_MODE = "ABNORMAL"  # 非预期状态，占位用于拒绝继续执行并等待人工介入


# ============================================================================
# price helpers
# ============================================================================

PRICE_TICK = 1.0


def format_price(price):
    return str(int(round(float(price))))


def price_to_ticks(price):
    return int(round(float(price) / PRICE_TICK))


def normalize_price(price):
    return float(price_to_ticks(price)) * PRICE_TICK


def prices_equal(price_a, price_b):
    return price_to_ticks(price_a) == price_to_ticks(price_b)


def price_gap_matches(buy_price, sell_price, expected_gap):
    return price_to_ticks(sell_price) - price_to_ticks(buy_price) == price_to_ticks(expected_gap)


def price_distance_at_least(high_price, low_price, distance):
    return price_to_ticks(high_price) - price_to_ticks(low_price) >= price_to_ticks(distance)


# ============================================================================
# logging helpers
# ============================================================================

_last_keep_type = None
_last_keep_ts = 0.0
GMT_PLUS_8 = timezone(timedelta(hours=8))


def log_msg(message):
    timestamp = datetime.now(GMT_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def log_keep_state(keep_type, message):
    global _last_keep_type
    global _last_keep_ts

    now = time.time()
    if keep_type != _last_keep_type or now - _last_keep_ts >= KEEP_LOG_INTERVAL_SEC:
        log_msg(message)
        _last_keep_type = keep_type
        _last_keep_ts = now


def summarize_orders(orders):
    if not orders:
        log_msg("OPEN ORDERS: none")
        return 0, 0

    parts = []
    buy_count = 0
    sell_count = 0

    for order in orders:
        parts.append(f"{order['side']} - {format_price(order['price'])}")
        if order["side"] == "BUY":
            buy_count += 1
        else:
            sell_count += 1

    log_msg(f"OPEN ORDERS | {' | '.join(parts)}")
    return buy_count, sell_count