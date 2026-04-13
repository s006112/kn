"""Infra snapshot reads and retry helpers for the grid engine."""

import random
import socket
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
    log_msg,
)


def get_open_orders(info):
    """作用:
    获取当前配置账户地址的未完成订单，并对短暂性 API/网络失败做有限重试。
    """
    return read_infra_with_retry(
        lambda: info.open_orders(ACCOUNT_ADDRESS),
        read_name="open-orders",
        max_retries=OPEN_ORDERS_MAX_RETRIES,
        base_delay_sec=OPEN_ORDERS_RETRY_BASE_SEC,
        max_delay_sec=OPEN_ORDERS_RETRY_MAX_SEC,
        jitter_max_sec=OPEN_ORDERS_RETRY_JITTER_MAX_SEC,
        cooldown_sec=OPEN_ORDERS_RETRY_COOLDOWN_SEC,
    )


def is_retryable_infra_read_error(exc):
    """作用:
    判断基础设施读取异常是否属于可重试的短暂性失败。
    """
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
    """作用:
    将基础设施异常格式化为紧凑日志文本。
    """
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
    """作用:
    执行一次基础设施快照读取，并对短暂性失败做有限重试。
    """
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
    - 透传有限重试后最后一次 `info.all_mids()` 或浮点转换抛出的异常
    """
    btc_mid = float(get_all_mids_with_retry(info).get(BTC_MID_KEY, 0))
    if btc_mid <= 0:
        raise ValueError("Failed to get BTC mid price")

    reference_price = float(int((btc_mid / GRID_STEP) + 0.5) * GRID_STEP)  # round-half-up
    if reference_price <= 0:
        raise ValueError("Invalid reference price")
    if reference_price - (BUY_GRID_FACTOR * GRID_STEP) <= 0:
        raise ValueError("Invalid buy price")

    return reference_price


def get_all_mids_with_retry(info):
    """作用:
    读取 BTC 中间价所需的 mids 快照，并对短暂性基础设施失败做轻量重试。
    """
    return read_infra_with_retry(
        info.all_mids,
        read_name="btc-mid",
        max_retries=BTC_MID_MAX_RETRIES,
        base_delay_sec=BTC_MID_RETRY_BASE_SEC,
        max_delay_sec=BTC_MID_RETRY_MAX_SEC,
    )


def read_current_btc_mid(info):
    """作用:
    读取当前 BTC 中间价；读取或解析失败时返回 `None`。
    """
    try:
        return float(info.all_mids().get(BTC_MID_KEY, 0))
    except Exception:
        return None
