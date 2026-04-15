"""Gateway reads, normalization, and retry helpers for the grid engine."""

import random
import socket
import threading
import time

from requests import exceptions as requests_exceptions

from hyperliquid.utils.error import ClientError, ServerError

from grid_config import (
    ACCOUNT_ADDRESS,
    BTC_MID_KEY,
    BTC_MID_MAX_RETRIES,
    BTC_MID_RETRY_BASE_SEC,
    BTC_MID_RETRY_MAX_SEC,
    BUY_GRID_FACTOR,
    GRID_STEP,
    OPEN_ORDERS_MAX_RETRIES,
    OPEN_ORDERS_RETRY_BASE_SEC,
    OPEN_ORDERS_RETRY_COOLDOWN_SEC,
    OPEN_ORDERS_RETRY_JITTER_MAX_SEC,
    OPEN_ORDERS_RETRY_MAX_SEC,
    OPEN_ORDERS_RETRYABLE_STATUS_CODES,
    normalize_price,
    log_msg,
)


class SnapshotFeed:
    def __init__(self, info, subscription, read_func, read_name):
        self.info = info
        self.subscription = subscription
        self.read_func = read_func
        self.read_name = read_name
        self._lock = threading.Lock()
        self._snapshot = None
        self._last_update_ts = 0.0
        self._last_event_ts = 0.0

    def start(self):
        subscribe = getattr(self.info, "subscribe", None)
        if subscribe is None:
            log_msg(f"ws: {self.read_name} subscribe unavailable, polling only")
            return False

        try:
            subscribe(self.subscription, self._on_message)
            log_msg(f"ws: {self.read_name} subscribed")
            return True
        except Exception as exc:
            log_msg(
                f"ws: {self.read_name} subscribe failed "
                f"({type(exc).__name__}: {exc}), polling only"
            )
            return False

    def seed(self, snapshot):
        with self._lock:
            self._snapshot = snapshot
            self._last_update_ts = time.time()

    def get_snapshot(self):
        with self._lock:
            return self._snapshot

    def refresh_from_poll(self):
        snapshot = self.read_func(self.info)
        self.seed(snapshot)
        return snapshot

    def _on_message(self, _msg):
        with self._lock:
            self._last_event_ts = time.time()

        try:
            self.refresh_from_poll()
        except Exception as exc:
            log_msg(
                f"ws: {self.read_name} refresh failed "
                f"({type(exc).__name__}: {exc})"
            )


class LiveOrdersFeed(SnapshotFeed):
    def __init__(self, info):
        super().__init__(
            info=info,
            subscription={"type": "userEvents", "user": ACCOUNT_ADDRESS},
            read_func=get_open_orders,
            read_name="userEvents",
        )


class BtcMidFeed(SnapshotFeed):
    def __init__(self, info):
        super().__init__(
            info=info,
            subscription={"type": "allMids"},
            read_func=read_current_btc_mid,
            read_name="allMids",
        )


def normalize_order(order):
    """将交易所原始订单映射为 engine 内部统一 shape。"""
    raw_side = order["side"]
    if raw_side == "B":
        side = "BUY"
    elif raw_side == "A":
        side = "SELL"
    else:
        raise ValueError(f"Unknown order side: {raw_side!r}")

    price = normalize_price(order["limitPx"])

    normalized = dict(order)
    normalized["side"] = side
    normalized["price"] = price
    normalized["raw_side"] = raw_side
    return normalized


def normalize_orders(orders):
    """将 open orders 列表标准化为统一 shape。"""
    return [normalize_order(order) for order in orders]


def get_open_orders(info):
    """通过 engine gateway 获取当前配置账户地址的未完成订单，并做有限重试与标准化。"""
    orders = read_infra_with_retry(
        lambda: info.open_orders(ACCOUNT_ADDRESS),
        read_name="open-orders",
        max_retries=OPEN_ORDERS_MAX_RETRIES,
        base_delay_sec=OPEN_ORDERS_RETRY_BASE_SEC,
        max_delay_sec=OPEN_ORDERS_RETRY_MAX_SEC,
        jitter_max_sec=OPEN_ORDERS_RETRY_JITTER_MAX_SEC,
        cooldown_sec=OPEN_ORDERS_RETRY_COOLDOWN_SEC,
    )
    return normalize_orders(orders)


def is_retryable_infra_read_error(exc):
    """判断 gateway 外部读取异常是否属于可重试的短暂性失败。"""
    status_code = getattr(exc, "status_code", None)
    if status_code in OPEN_ORDERS_RETRYABLE_STATUS_CODES:
        return True

    if isinstance(
        exc,
        (
            requests_exceptions.Timeout,
            requests_exceptions.ConnectionError,
            TimeoutError,
            ConnectionResetError,
            socket.timeout,
        ),
    ):
        return True

    if isinstance(exc, OSError):
        message = str(exc).lower()
        transient_markers = (
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
            "temporary failure",
            "temporarily unavailable",
        )
        return any(marker in message for marker in transient_markers)

    return False


def format_retryable_exception(exc):
    """将 gateway 外部读取异常格式化为紧凑日志文本。"""
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        if isinstance(exc, ClientError):
            return f"{type(exc).__name__} status={status_code} code={exc.error_code}"
        if isinstance(exc, ServerError):
            return f"{type(exc).__name__} status={status_code}"
        return f"{type(exc).__name__} status={status_code}"

    return f"{type(exc).__name__}: {exc}"


def read_infra_with_retry(
    read_func,
    read_name,
    max_retries,
    base_delay_sec,
    max_delay_sec,
    jitter_max_sec=0.0,
    cooldown_sec=0.0,
):
    """执行一次 gateway 外部快照读取，并对短暂性失败做有限重试。"""
    for attempt in range(max_retries + 1):
        try:
            return read_func()
        except Exception as exc:
            if not is_retryable_infra_read_error(exc):
                raise

            if attempt >= max_retries:
                log_msg(
                    f"infra: {read_name} read failed after retries "
                    f"({format_retryable_exception(exc)})"
                )
                raise

            retry_number = attempt + 1
            delay_sec = min(base_delay_sec * (2 ** attempt), max_delay_sec)
            if jitter_max_sec > 0:
                delay_sec += random.uniform(0.0, jitter_max_sec)

            log_msg(
                f"infra: {read_name} transient failure, "
                f"retry {retry_number}/{max_retries} "
                f"in {delay_sec:.2f}s ({format_retryable_exception(exc)})"
            )
            time.sleep(delay_sec)
            if cooldown_sec > 0:
                time.sleep(cooldown_sec)


def get_mid_reference_price(info):
    """根据当前 BTC 中间价和配置的网格步长推导参考价（对齐到最近网格）。"""
    btc_mid = float(get_all_mids_with_retry(info).get(BTC_MID_KEY, 0))
    if btc_mid <= 0:
        raise ValueError("Failed to get BTC mid price")

    reference_price = normalize_price(int((btc_mid / GRID_STEP) + 0.5) * GRID_STEP)
    if reference_price <= 0:
        raise ValueError("Invalid reference price")
    if reference_price - (BUY_GRID_FACTOR * GRID_STEP) <= 0:
        raise ValueError("Invalid buy price")

    return reference_price


def get_all_mids_with_retry(info):
    """读取 BTC 中间价所需的 mids 快照，并对短暂性 gateway 读取失败做轻量重试。"""
    return read_infra_with_retry(
        info.all_mids,
        read_name="btc-mid",
        max_retries=BTC_MID_MAX_RETRIES,
        base_delay_sec=BTC_MID_RETRY_BASE_SEC,
        max_delay_sec=BTC_MID_RETRY_MAX_SEC,
    )


def read_current_btc_mid(info):
    """读取当前 BTC 中间价；读取或解析失败时记录日志并返回 None。"""
    try:
        btc_mid = float(get_all_mids_with_retry(info).get(BTC_MID_KEY, 0))
    except Exception as exc:
        log_msg(f"infra: btc-mid unavailable ({type(exc).__name__}: {exc})")
        return None

    if btc_mid <= 0:
        log_msg(f"infra: btc-mid invalid ({btc_mid})")
        return None

    return normalize_price(btc_mid)