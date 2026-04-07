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
API_KEY = os.getenv("HYPERLIQUID_API_KEY")
ACCOUNT_ADDRESS = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")

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
    btc_price = float(all_mids.get("@142", 0))
    
    if btc_price == 0:
        print("无法获取 BTC 价格，请检查网络。")
        return

    budget_usdc = 50.0
    
    # --- 修复核心：价格取整以符合 Spot Tick Size ---
    limit_price = float(int(btc_price * 0.995)) 
    btc_size = round(budget_usdc / limit_price, 5)

    print(f"\n当前 BTC 市价: ${btc_price}")
    print(f"修正后的挂单价: ${limit_price}")
    print(f"预计买入数量: {btc_size} BTC")

    # --- 3. 提交订单 ---
    print("\n[Action] 正在按照位置参数提交现货订单...")
    
    try:
        # Hyperliquid SDK 里 "BTC" 会映射到 perp 资产；现货 BTC/USDC 对应 UBTC/USDC。
        spot_coin = "UBTC/USDC"
        
        # 核心修复点：
        # 如果你的 SDK 支持，exchange.order 的最后一个位置参数通常是为 Spot 准备的辅助参数
        # 这里我们严格按照你之前跑通的位置参数顺序，但确保环境处于 Spot 状态
        order_result = exchange.order(
            spot_coin,   # name: UBTC/USDC
            True,        # is_buy: True
            btc_size,    # sz: 数量
            limit_price, # limit_px: 价格 (整数)
            {"limit": {"tif": "Gtc"}}, # order_type
            False        # reduce_only: 现货必须为 False
        )

        # --- 4. 状态处理 ---
        if order_result["status"] == "ok":
            status_data = order_result["response"]["data"]["statuses"][0]
            
            if "resting" in status_data:
                oid = status_data["resting"]["oid"]
                print(f"✅ 成功: 现货订单已挂起! Order ID: {oid}")
                
                time.sleep(1)
                order_status = info.query_order_by_oid(ACCOUNT_ADDRESS, oid)
                
                # 检查返回的状态，确保没有 "leveraged" 或 "liquidation" 字样
                print(f"🔍 实时状态确认:")
                print(json.dumps(order_status, indent=2))
            elif "filled" in status_data:
                print("⚡ 订单已立即成交 (Filled)")
            else:
                print(f"⚠️ 订单状态: {json.dumps(status_data)}")
        else:
            print(f"❌ 失败: {order_result}")
            
    except Exception as e:
        print(f"程序运行过程中捕获到异常: {e}")

if __name__ == "__main__":
    run_spot_limit_buy()
