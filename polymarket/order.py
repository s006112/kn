#!/usr/bin/env python3

import time
import logging
import os
from py_clob_client.client import ClobClient         #pip install py-clob-client
from py_clob_client.clob_types import OrderArgs
from py_clob_client.constants import POLYGON

# 導入 gamma.py 的工具函式
import gamma

# --- 配置區 ---
# 請至 Polymarket 設定頁面獲取以下資訊
PK = os.getenv("PK", "你的錢包私鑰")
API_KEY = os.getenv("API_KEY", "你的API_KEY")
API_SECRET = os.getenv("API_SECRET", "你的SECRET")
API_PASSPHRASE = os.getenv("API_PASSPHRASE", "你的PASSPHRASE")

# 策略參數
USD_INVESTMENT = 5.0      # 每次預計投入的美元金額 (建議至少 5.0)
BUY_LIMIT_PRICE = 0.45    # 掛買價格
BUY_THRESHOLD = 0.46      # 觸發掛買的市場價格門檻
SELL_LIMIT_PRICE = 0.50   # 掛賣價格

# 日誌設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Trader")

def get_clob_client():
    """初始化 Polymarket CLOB 客戶端"""
    return ClobClient(
        host="https://clob.polymarket.com",
        key=PK,
        chain_id=137, # Polygon Mainnet
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE
    )

def get_token_balance(client, token_id):
    """獲取特定 Token 的持倉數量"""
    try:
        # 獲取餘額，API 回傳通常包含多個 token，需篩選
        balance_info = client.get_balance(token_id)
        return float(balance_info.get("balance", 0))
    except Exception as e:
        logger.error(f"獲取餘額失敗: {e}")
        return 0.0

def has_open_order(client, token_id, side):
    """檢查是否有該 Token 的活躍掛單 (BUY/SELL)"""
    try:
        open_orders = client.get_open_orders()
        for order in open_orders:
            # 確保欄位名稱符合 SDK 回傳格式
            if order.get("token_id") == token_id and order.get("side") == side:
                return True
        return False
    except Exception as e:
        logger.error(f"獲取掛單列表失敗: {e}")
        return False

def main():
    client = get_clob_client()
    logger.info("交易機器人已啟動...")

    while True:
        try:
            # 1. 尋找目標市場 (使用 gamma.py 的邏輯)
            try:
                rounds = gamma.find_current_rounds()
            except RuntimeError:
                logger.info("目前沒有符合條件的 BTC 15m 市場，等待中...")
                time.sleep(30)
                continue

            # 挑選最接近結束的市場
            target_market = rounds[0]
            up_token = target_market['up_token']
            end_ts = target_market['end_ts']
            title = target_market['title']
            
            now = int(time.time())
            t_remain = end_ts - now

            # 2. 獲取當前帳戶狀態
            position_size = get_token_balance(client, up_token)
            
            # --- 買入邏輯 (Buy Up) ---
            # 條件：840 <= t_remain <= 1020 且 無持倉 且 無活躍買單
            if 840 <= t_remain <= 1020:
                if position_size <= 0 and not has_open_order(client, up_token, "BUY"):
                    # 檢查價格門檻
                    book = gamma.get_book(up_token)
                    # 取得 Best Ask (賣一價)
                    best_ask = gamma._f(book.get("asks", [{}])[-1].get("price")) if book.get("asks") else None
                    
                    if best_ask and best_ask > BUY_THRESHOLD:
                        # 計算下單數量 (Shares = USD / Price)
                        shares = round(USD_INVESTMENT / BUY_LIMIT_PRICE, 2)
                        
                        logger.info(f"符合買入條件！市場: {title}, t_remain: {t_remain}s, Best Ask: {best_ask}")
                        resp = client.create_order(OrderArgs(
                            price=BUY_LIMIT_PRICE,
                            size=shares,
                            side="BUY",
                            token_id=up_token
                        ))
                        logger.info(f"買單已發送: {resp}")

            # --- 賣出邏輯 (Sell Up) ---
            # 條件：買單成交後有持倉 且 無活躍賣單
            if position_size > 0:
                if not has_open_order(client, up_token, "SELL"):
                    logger.info(f"偵測到持倉 ({position_size} shares)，準備掛賣 @ {SELL_LIMIT_PRICE}")
                    
                    resp = client.create_order(OrderArgs(
                        price=SELL_LIMIT_PRICE,
                        size=position_size,
                        side="SELL",
                        token_id=up_token
                    ))
                    logger.info(f"賣單已發送: {resp}")

            # 狀態打印
            status_msg = f"[{title}] t_remain: {t_remain:>4}s | Pos: {position_size:>6}"
            print(status_msg, end="\r")

        except Exception as e:
            logger.error(f"主循環異常: {e}")
            time.sleep(10)

        time.sleep(5) # 避免過於頻繁請求 API

if __name__ == "__main__":
    main()