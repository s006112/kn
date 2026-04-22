import os
import socket
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from hyperliquid.utils.error import ClientError
from requests import exceptions as requests_exceptions

load_dotenv()

# ============================================================================
# env / account config
# ============================================================================

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_WALLET_KEY = os.getenv("HYPERLIQUID_API_WALLET_KEY")

SYMBOL = "UBTC/USDC"
BTC_MID_KEY = "@142"

BUDGET_USDC = 200.0  # 每侧订单按 100 USDC 名义金额下单，用它反推买卖数量
GRID_STEP = 200.0  # 参考价每上下偏移 100 美元挂一格，是网格的基础间距
BUY_GRID_FACTOR =0.90  # 买单距离 = 1.00 x GRID_STEP，1.00 表示向下 1 格挂买单
SELL_GRID_FACTOR = 1.11  # 卖单距离 = 1.00 x GRID_STEP，1.00 表示向上 1 格挂卖单
GRID_GAP = (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP  # 买卖单之间的价差，理论上应该保持稳定

TICK_COUNT = 40  # 价格偏移超过 N 格时，认为价格发生了较大变动，可能需要重锚
MAIN_LOOP_POLL_INTERVAL_SEC = 1.0  # 主循环，检查挂单和状态是否变化
CONSECUTIVE_READS = 2  # btc_mid 连续不变读数窗口
ORDER_ZONE = GRID_GAP * 0.03 # 订单区间，价格偏移在网格间距的 xx% 以内时，认为价格在订单附近

ALLOW_BUY_ONLY_WHEN_NO_BTC = True  # 卖单因 BTC 不足下不出时，允许退化成仅挂买单
ALLOW_SELL_ONLY_WHEN_NO_USDC = True  # 买单因 USDC 不足下不出时，允许退化成仅挂卖单

REANCHOR_BREAK = True  # 启用 BUY_ONLY 模式下的重锚检测，偏离太远时整组重建
REANCHOR_BREAK_STEPS = 2  # 与 BTC 中间价相差达到 2 格时，触发 anchor break 重挂
REANCHOR_DISTANCE = (BUY_GRID_FACTOR + REANCHOR_BREAK_STEPS) * GRID_STEP    # 当 BTC mid 与当前 buy order 距离 >= REANCHOR_DISTANCE 时触发 anchor break

KEEP_LOG_INTERVAL_SEC = 900  # keep 状态同类日志至少每 900 秒打印一次，避免刷屏

WAIT_NO_OPEN_ORDERS_INTERVAL_SEC = 0.5  # 撤单后检查一次是否已无遗留挂单

MAX_RETRIES = 4  # 读取接口最多重试 4 次，容忍短暂接口抖动
RETRY_SEC = 0.5  # 读取接口重试的固定等待时间

def apply_runtime_overrides(overrides: dict):
    globals_ = globals()
    for k, v in overrides.items():
        if k in globals_:
            globals_[k] = v

# ============================================================================
# state / mode constants
# ============================================================================

PAIR_MODE = "PAIR"  # 正常双边模式，同时有 1 买 1 卖且价差符合网格规则
BUY_ONLY_MODE = "BUY_ONLY"  # 仅剩买单的残边模式，常见于卖侧因余额不足未成功挂出
SELL_ONLY_MODE = "SELL_ONLY"  # 仅剩卖单的残边模式，常见于买侧因 USDC 不足未成功挂出
ABNORMAL_MODE = "ABNORMAL"  # 非预期状态，占位用于拒绝继续执行并等待人工介入


# ============================================================================
# price helpers
# ============================================================================

def normalize_price(price):
    # enforce integer price alignment across all internal comparisons
    return float(round(float(price)))

def format_price(price):
    return str(int(normalize_price(price)))

def price_gap_matches(buy_price, sell_price, expected_gap):
    #return normalize_price(sell_price) - normalize_price(buy_price) == normalize_price(expected_gap)
    return True

def price_distance_at_least(high_price, low_price, distance):
    return normalize_price(high_price) - normalize_price(low_price) >= normalize_price(distance)

# ============================================================================
# logging helpers
# ============================================================================

_last_keep_type = None
_last_keep_ts = 0.0
GMT_PLUS_8 = timezone(timedelta(hours=8))


def log_msg(message):
    timestamp = datetime.now(GMT_PLUS_8).strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")


def log_keep_state(keep_type, message):
    global _last_keep_type
    global _last_keep_ts

    now = time.time()
    if keep_type != _last_keep_type or now - _last_keep_ts >= KEEP_LOG_INTERVAL_SEC:
        log_msg(message)
        _last_keep_type = keep_type
        _last_keep_ts = now

# ============================================================================
# grid read and summarise order helpers
# ============================================================================

def normalize_order(order): # 将接口返回的订单结构标准化为 {"side": "BUY"|"SELL", "price": float, ...} 的格式，方便后续处理
    raw_side = order["side"]
    if raw_side == "B":
        side = "BUY"
    elif raw_side == "A":
        side = "SELL"
    else:
        raise ValueError(f"Unknown order side: {raw_side!r}")

    normalized = dict(order)
    normalized["side"] = side
    normalized["price"] = normalize_price(order["limitPx"])
    return normalized


def read_btc_mid(info):
    btc_mid = float(retry_read("btc-mid", info.all_mids).get(BTC_MID_KEY, 0))

    if btc_mid <= 0:
        return None

    return normalize_price(btc_mid)


def read_btc_grid(info):    # 以 BTC mid 作为参考价，计算网格锚点价格
    btc_mid = read_btc_mid(info)

    if btc_mid is None:
        raise ValueError("Failed to get BTC mid price")

    return int((btc_mid / GRID_STEP) + 0.5) * GRID_STEP



def summarize_orders(orders):   # 打印当前挂单的简要信息，返回买单和卖单的数量
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


def retry_read(name, fn, retries=MAX_RETRIES, delay=RETRY_SEC):
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            retryable = status in {429, 502, 503, 504}

            if not retryable and isinstance(exc, (
                requests_exceptions.Timeout,
                requests_exceptions.ConnectionError,
                TimeoutError,
                ConnectionResetError,
                socket.timeout,
                OSError,
            )):
                msg = str(exc).lower()
                retryable = any(k in msg for k in ("timeout", "connection", "temporarily"))

            if not retryable:
                raise

            if status is None:
                err = f"{type(exc).__name__}: {exc}"
            elif isinstance(exc, ClientError):
                err = f"{type(exc).__name__} status={status} code={exc.error_code}"
            else:
                err = f"{type(exc).__name__} status={status}"

            if attempt == retries:
                log_msg(f"{name} failed after {retries} retries: {err}")
                raise

            log_msg(f"{name} retry {attempt + 1}/{retries} ({err})")
            time.sleep(delay)


def read_orders(info):
    return [
        normalize_order(order)
        for order in retry_read("open-orders", lambda: info.open_orders(ACCOUNT_ADDRESS))
    ]