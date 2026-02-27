import time
import logging
import requests
from clob_connect import get_poly_client
from py_clob_client.clob_types import OrderArgs
import gamma

# 設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("PolyBot")

# 策略常數與開關
DRY_RUN = True  # True 則不實行下單，僅打印 log
INVEST_USD, BUY_LIMIT, BUY_TRIGGER, SELL_LIMIT = 5.0, 0.50, 0.51, 0.55
ORDERS_API_AVAILABLE = True


def get_position_balance(address, token_id):
    """讀取指定 token 的持倉（免 API key）。"""
    try:
        r = requests.get(
            "https://data-api.polymarket.com/positions",
            params={"user": address, "sizeThreshold": 0},
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json() or []
        for row in rows:
            if str(row.get("asset")) == str(token_id):
                return float(row.get("size", 0) or 0)
    except Exception:
        pass
    return 0.0


def get_open_orders(client, token_id):
    """
    相容不同 py_clob_client 版本的 open orders 取得方式。
    DRY_RUN 模式下不呼叫 CLOB 私有 API（避免 401）。
    """
    global ORDERS_API_AVAILABLE

    if DRY_RUN:
        return []

    if not ORDERS_API_AVAILABLE:
        return []

    try:
        orders = client.get_orders()

        if isinstance(orders, dict):
            orders = orders.get("data", [])

        if not isinstance(orders, list):
            return []

        return [o for o in orders if str(o.get("asset_id")) == str(token_id)]

    except requests.exceptions.HTTPError as e:
        if getattr(e.response, "status_code", None) == 401:
            logger.error("CLOB API 401 Unauthorized - check API creds / wallet binding")
        ORDERS_API_AVAILABLE = False
        return []

    except Exception as e:
        logger.error(f"get_open_orders error: {e}")
        ORDERS_API_AVAILABLE = False
        return []


def run_trade(client, side, size, price, token_id):
    """執行或模擬下單"""
    args = OrderArgs(price=price, size=size, side=side, token_id=token_id)

    if DRY_RUN:
        logger.info(f"[DRY_RUN] {side} {size} @ {price}")
        return

    logger.info(f"實行下單: {side} {size} @ {price}")
    return client.create_order(args)


def main():
    client = get_poly_client()
    if not client:
        return

    address = client.get_address()
    logger.info(f"啟動 地址: {address} | DRY_RUN: {DRY_RUN}")

    target = None
    target_refresh_ts = 0

    while True:
        try:
            now = int(time.time())

            # --- 每 60 秒刷新一次市場 ---
            if not target or now - target_refresh_ts > 60:
                rounds = gamma.find_current_rounds()
                if not rounds:
                    raise Exception("No active rounds")
                target = rounds[0]
                target_refresh_ts = now

            token, end_ts = target['up_token'], target['end_ts']
            t_remain = end_ts - now

            balance = get_position_balance(address, token)
            orders = get_open_orders(client, token)
            has_side = lambda s: any(str(o.get('side', '')).upper() == s for o in orders)

            # 買入
            if 840 <= t_remain <= 1020 and balance == 0 and not has_side("BUY"):
                book = gamma.get_book(token)
                ask = gamma._f(book.get("asks", [{}])[-1].get("price"))
                if ask and ask > BUY_TRIGGER:
                    run_trade(client, "BUY", round(INVEST_USD / BUY_LIMIT, 2), BUY_LIMIT, token)

            # 賣出
            if balance > 0 and not has_side("SELL"):
                run_trade(client, "SELL", balance, SELL_LIMIT, token)

            print(
                f"Time: {t_remain:>4}s | Pos: {balance:>6} | Market: {target['title'][:15]}",
                end="\r"
            )

        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(0)

        time.sleep(0)

if __name__ == "__main__":
    main()