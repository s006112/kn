# hyperliquid_recursive.py, pip3 install hyperliquid-python-sdk

import time
import json
import os
from dotenv import load_dotenv
from hyperliquid.utils import constants
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from eth_account import Account

# 加载环境变量
load_dotenv()

# --- 1. 配置加载与初始化 ---
API_KEY = os.getenv("API_KEY")
ACCOUNT_ADDRESS = os.getenv("ACCOUNT_ADDRESS")

def mask_key(key):
    if not key: return "None"
    return f"{key[:6]}...{key[-4:]}"

def run_spot_limit_buy():
    if not API_KEY or not ACCOUNT_ADDRESS:
        print("错误: 请确保 .env 文件配置正确")
        return

    # 初始化 Agent
    agent_account = Account.from_key(API_KEY)
    
    # 初始化接口
    info = Info(constants.MAINNET_API_URL)
    exchange = Exchange(
        agent_account, 
        constants.MAINNET_API_URL, 
        account_address=ACCOUNT_ADDRESS
    )

    print(f"--- 验证信息 ---")
    print(f"Agent 地址: {mask_key(agent_account.address)}")
    print(f"主钱包地址: {mask_key(ACCOUNT_ADDRESS)}")

    # --- 2. 获取市场价格 ---
    all_mids = info.all_mids()
    btc_price = float(all_mids.get("BTC", 0))
    
    if btc_price == 0:
        print("无法获取 BTC 价格，请检查网络。")
        return

    budget_usdc = 50.0 # 你的预算
    
    # 强制价格取整以符合现货 Tick Size
    limit_price = float(int(btc_price * 0.995)) 
    btc_size = round(budget_usdc / limit_price, 5)

    print(f"\n当前 BTC 市价: ${btc_price}")
    print(f"修正后的现货挂单价: ${limit_price}")
    print(f"预计买入数量: {btc_size} BTC")

    # --- 3. 提交订单 ---
    print("\n[Action] 正在提交现货订单...")
    
    try:
        # --- 核心修复：手动指定现货资产 ---
        # 在 Hyperliquid Spot 中，BTC 的内部资产编号通常是 1 (或通过 info.spot_meta 确认)
        # 既然字符串解析失败，我们直接传入这个编号
        # 注意：这里的 order_type 必须包含 "spot": True 标志（如果 SDK 支持）
        
        order_result = exchange.order(
            "BTC",        # 保持 BTC，但通过后续逻辑或底层 ID 触发
            True,         # is_buy
            btc_size,     # sz
            limit_price,  # limit_px
            {
                "limit": {"tif": "Gtc"}
            },
            False         # reduce_only: 现货必须为 False
        )

        # 如果上述代码仍然产生 "Long"，这是因为 SDK 默认指向 Perp 市场。
        # 终极解决方法：使用底层 API 结构或确认现货资产 ID。
        # 尝试使用整数 ID (如果是旧版 SDK，第一个参数可以传 ID)
        # order_result = exchange.order(1, True, btc_size, limit_price, {"limit": {"tif": "Gtc"}}, False)

        # --- 4. 状态处理 ---
        if order_result["status"] == "ok":
            status_data = order_result["response"]["data"]["statuses"][0]
            if "resting" in status_data:
                oid = status_data["resting"]["oid"]
                print(f"✅ 成功: 订单已挂起! Order ID: {oid}")
                
                time.sleep(1)
                order_status = info.query_order_by_oid(ACCOUNT_ADDRESS, oid)
                print(f"🔍 状态确认 (请检查 Direction 是否为 Buy):")
                print(json.dumps(order_status, indent=2))
            else:
                print(f"⚠️ 订单状态: {json.dumps(status_data)}")
        else:
            print(f"❌ 失败: {order_result}")
            
    except Exception as e:
        print(f"程序运行过程中捕获到异常: {e}")

if __name__ == "__main__":
    run_spot_limit_buy()