import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

# 載入 .env 檔案
load_dotenv()

def get_poly_client():
    """高效初始化並回傳 Polymarket 客戶端"""
    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("METAMASK_L1_KEY"),
            chain_id=137
        )
        client.set_api_creds(ApiCreds(
            api_key=os.getenv("POLYMARKET_API_KEY"),
            api_secret=os.getenv("POLYMARKET_API_SECRET"),
            api_passphrase=os.getenv("POLYMARKET_API_PASSPHRASE")
        ))
        return client
    except Exception as e:
        print(f"❌ 初始化失敗: {e}")
        return None

if __name__ == "__main__":
    client = get_poly_client()
    if client:
        print(f"✅ 連線成功！地址: {client.get_address()}")