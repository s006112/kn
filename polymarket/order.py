import time
import logging
from clob_connect import get_poly_client  # 直接調用剛寫好的初始化函式
from py_clob_client.clob_types import OrderArgs
import gamma

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("PolyBot")

# 策略常數
INVEST_USD = 5.0
BUY_LIMIT = 0.45
BUY_TRIGGER = 0.46
SELL_LIMIT = 0.50

def main():
    client = get_poly_client()
    if not client: return
    
    logger.info(f"機器人啟動。地址: {client.get_address()}")

    while True:
        try:
            # 1. 市場發現
            rounds = gamma.find_current_rounds()
            if not rounds:
                time.sleep(10); continue
            
            target = rounds[0]
            up_token = target['up_token']
            t_remain = target['end_ts'] - int(time.time())

            # 2. 狀態檢查 (持倉與掛單)
            balance = float(client.get_balance(up_token).get("balance", 0))
            open_orders = client.get_open_orders()
            has_buy = any(o.get("token_id") == up_token and o.get("side") == "BUY" for o in open_orders)
            has_sell = any(o.get("token_id") == up_token and o.get("side") == "SELL" for o in open_orders)

            # 3. 買入規則 (840s <= t_remain <= 1020s)
            if 840 <= t_remain <= 1020 and balance == 0 and not has_buy:
                book = gamma.get_book(up_token)
                best_ask = gamma._f(book.get("asks", [{}])[-1].get("price"))
                
                if best_ask and best_ask > BUY_TRIGGER:
                    shares = round(INVEST_USD / BUY_LIMIT, 2)
                    logger.info(f"下買單: {shares} shares @ {BUY_LIMIT}")
                    client.create_order(OrderArgs(price=BUY_LIMIT, size=shares, side="BUY", token_id=up_token))

            # 4. 賣出規則
            if balance > 0 and not has_sell:
                logger.info(f"下賣單: {balance} shares @ {SELL_LIMIT}")
                client.create_order(OrderArgs(price=SELL_LIMIT, size=balance, side="SELL", token_id=up_token))

            print(f"Time: {t_remain}s | Pos: {balance} | Market: {target['title'][:15]}", end="\r")

        except Exception as e:
            logger.error(f"Error: {e}"); time.sleep(10)
        
        time.sleep(5)

if __name__ == "__main__":
    main()