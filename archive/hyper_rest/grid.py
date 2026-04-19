"""Engine shell and entrypoint for the Hyperliquid spot grid."""

import time

from eth_account import Account

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from grid_decision import get_current_pair_state, get_loop_action
from grid_execution import rebuild
from grid_infra import get_open_orders
from grid_config import (
    ABNORMAL_MODE,
    ACCOUNT_ADDRESS,
    API_WALLET_KEY,
    MAIN_LOOP_POLL_INTERVAL_SEC,
    log_msg,
    summarize_orders,
)


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
    返回 `None`。当动作持续解析为 `keep` 或成功的 `rebuild` 时无限循环；当重建失败、重建结果异常，或动作解析为 abnormal 时立即退出。过程中会记录异常摘要与退出条件。
    """
    while True:
        time.sleep(MAIN_LOOP_POLL_INTERVAL_SEC)
        orders = get_open_orders(info)
        action, reference_price = get_loop_action(info, orders, state)

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
    为当前配置的 API wallet key 和账户地址构造 Hyperliquid 交易客户端。

    输入:
    无。

    输出:
    返回绑定到 `constants.MAINNET_API_URL` 和 `ACCOUNT_ADDRESS` 的 `Exchange` 实例。当 `API_WALLET_KEY` 缺失时抛出 `ValueError`。透传 `Account.from_key()` 或 `Exchange` 抛出的异常。
    """
    if not API_WALLET_KEY:
        raise ValueError("Missing HYPERLIQUID_API_WALLET_KEY")

    return Exchange(
        Account.from_key(API_WALLET_KEY),
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
        state = get_current_pair_state(orders)
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
