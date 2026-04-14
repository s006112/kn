"""Shared runtime config and lightweight logging helpers for the grid engine."""

import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
API_KEY = os.getenv("HYPERLIQUID_API_KEY")

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
    timestamp = datetime.now(GMT_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def format_price(price):
    return str(int(round(float(price))))


def log_keep_state(keep_type, message):
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
    if not orders:
        log_msg("OPEN ORDERS: none")
        return 0, 0

    parts = []
    buy_count = 0
    sell_count = 0

    for order in orders:
        side = order["side"]
        parts.append(f"{side} - {format_price(order['price'])}")
        if side == "BUY":
            buy_count += 1
        else:
            sell_count += 1

    log_msg(f"OPEN ORDERS | {' | '.join(parts)}")
    return buy_count, sell_count