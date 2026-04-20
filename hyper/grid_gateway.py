"""Gateway reads and retry helpers for the grid engine."""

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
    normalize_price,
)


def normalize_order(order):
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


def is_retryable_read_error(exc):
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
        return any(
            marker in message
            for marker in (
                "timeout",
                "timed out",
                "connection reset",
                "connection aborted",
                "temporary failure",
                "temporarily unavailable",
            )
        )

    return False


def format_read_error(exc):
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        return f"{type(exc).__name__}: {exc}"

    if isinstance(exc, ClientError):
        return f"{type(exc).__name__} status={status_code} code={exc.error_code}"
    return f"{type(exc).__name__} status={status_code}"


def read_with_retry(
    read_func,
    read_name,
    max_retries,
    base_delay_sec,
    max_delay_sec,
    jitter_max_sec=0.0,
    cooldown_sec=0.0,
):
    for attempt in range(max_retries + 1):
        try:
            return read_func()
        except Exception as exc:
            if not is_retryable_read_error(exc):
                raise

            if attempt >= max_retries:
                log_msg(f"infra failure: {read_name} after retries ({format_read_error(exc)})")
                raise

            delay_sec = min(base_delay_sec * (2 ** attempt), max_delay_sec)
            if jitter_max_sec > 0:
                delay_sec += random.uniform(0.0, jitter_max_sec)

            log_msg(
                f"infra retry: {read_name} {attempt + 1}/{max_retries} "
                f"in {delay_sec:.2f}s ({format_read_error(exc)})"
            )
            time.sleep(delay_sec)
            if cooldown_sec > 0:
                time.sleep(cooldown_sec)


def read_orders(info):
    orders = read_with_retry(
        lambda: info.open_orders(ACCOUNT_ADDRESS),
        read_name="open-orders",
        max_retries=OPEN_ORDERS_MAX_RETRIES,
        base_delay_sec=OPEN_ORDERS_RETRY_BASE_SEC,
        max_delay_sec=OPEN_ORDERS_RETRY_MAX_SEC,
        jitter_max_sec=OPEN_ORDERS_RETRY_JITTER_MAX_SEC,
        cooldown_sec=OPEN_ORDERS_RETRY_COOLDOWN_SEC,
    )
    return [normalize_order(order) for order in orders]


def read_all_mids(info):
    return read_with_retry(
        info.all_mids,
        read_name="btc-mid",
        max_retries=BTC_MID_MAX_RETRIES,
        base_delay_sec=BTC_MID_RETRY_BASE_SEC,
        max_delay_sec=BTC_MID_RETRY_MAX_SEC,
    )


def get_reference_price(info):
    btc_mid = float(read_all_mids(info).get(BTC_MID_KEY, 0))
    if btc_mid <= 0:
        raise ValueError("Failed to get BTC mid price")

    reference_price = normalize_price(int((btc_mid / GRID_STEP) + 0.5) * GRID_STEP)
    if reference_price <= 0:
        raise ValueError("Invalid reference price")
    if reference_price - (BUY_GRID_FACTOR * GRID_STEP) <= 0:
        raise ValueError("Invalid buy price")

    return reference_price


def read_btc_mid(info):
    try:
        btc_mid = float(read_all_mids(info).get(BTC_MID_KEY, 0))
    except Exception as exc:
        log_msg(f"infra failure: btc-mid unavailable ({type(exc).__name__}: {exc})")
        return None

    if btc_mid <= 0:
        log_msg(f"infra failure: btc-mid invalid ({btc_mid})")
        return None

    return normalize_price(btc_mid)