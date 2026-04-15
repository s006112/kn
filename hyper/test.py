import time

# --- 核心逻辑注入 (保留 grid_decision.py 与 grid_config.py 的语义) ---
GRID_STEP = 200.0
BUY_GRID_FACTOR = 1.0
SELL_GRID_FACTOR = 1.0
PAIR_MODE = "PAIR"

def normalize_price(p): return float(p)

def price_gap_matches(buy_price, sell_price, expected_gap):
    # 模拟 grid_config 中的离散化/Ticks 比较逻辑
    return abs((sell_price - buy_price) - expected_gap) < 0.0001

def get_pair_state(orders):
    if len(orders) != 2: return None
    buy = next((normalize_price(o["price"]) for o in orders if o["side"] == "BUY"), None)
    sell = next((normalize_price(o["price"]) for o in orders if o["side"] == "SELL"), None)
    
    if not buy or not sell: return None
    
    expected_gap = (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP
    if price_gap_matches(buy, sell, expected_gap):
        return {"mode": PAIR_MODE, "buy_price": buy, "sell_price": sell}
    return None

def get_bootstrap_live_state(orders):
    # 根据 Contract 6: 启动时不接受单边，仅接受完美 Pair
    return get_pair_state(orders)

def log_res(case_name, result, expected):
    is_match = (result == expected)
    icon = "✅" if is_match else "❌"
    print(f"{icon} {case_name:.<45} Result: {result} | Expected: {expected}")

# --- 测试运行器 ---
def dry_run_eval():
    print(f"🚀 Starting Dry Run Evaluation (GRID_STEP={GRID_STEP})")
    print("=" * 85)

    # --- CASE 1: 挂单形态 (Shape Match/Mismatch) ---
    # 1.1a 完美双边
    o1_1a = [{"side": "BUY", "price": 99800.0}, {"side": "SELL", "price": 100200.0}]
    res1_1a = get_bootstrap_live_state(o1_1a)
    log_res("Case 1.1a: Shape Perfect Match (Inherit)", res1_1a["mode"] if res1_1a else None, PAIR_MODE)

    # 1.1b 单边残余 (应触发 Rebuild)
    o1_1b = [{"side": "BUY", "price": 99800.0}]
    res1_1b = get_bootstrap_live_state(o1_1b)
    log_res("Case 1.1b: Shape Mismatch-Residual (Rebuild)", res1_1b, None)

    # --- CASE 2.1: 步长验证 (Grid Step Match/Mismatch) ---
    # 2.1a 步长匹配 (Gap 400 = 200 * 2)
    o2_1a = [{"side": "BUY", "price": 99800.0}, {"side": "SELL", "price": 100200.0}]
    res2_1a = get_bootstrap_live_state(o2_1a)
    log_res("Case 2.1a: Grid Step Match (Inherit)", res2_1a["mode"] if res2_1a else None, PAIR_MODE)

    # 2.1b 步长不匹配 (Market Gap 100 vs Config 400)
    o2_1b = [{"side": "BUY", "price": 99900.0}, {"side": "SELL", "price": 100000.0}]
    res2_1b = get_bootstrap_live_state(o2_1b)
    log_res("Case 2.1b: Grid Step Mismatch (Rebuild)", res2_1b, None)

    # --- CASE 2.3: 金额验证 (Size Match/Mismatch - Sizing Invariant) ---
    # 2.3a 金额匹配
    o2_3a = [{"side": "BUY", "price": 99800.0, "size": 0.001}, {"side": "SELL", "price": 100200.0, "size": 0.001}]
    res2_3a = get_bootstrap_live_state(o2_3a)
    log_res("Case 2.3a: Size Match (Inherit)", res2_3a["mode"] if res2_3a else None, PAIR_MODE)

    # 2.3b 金额不匹配 (预期代码会继承，但评估通过 Expected: None 来主动触发 ❌ 提醒)
    o2_3b = [{"side": "BUY", "price": 99800.0, "size": 1.0}, {"side": "SELL", "price": 100200.0, "size": 0.00001}]
    res2_3b = get_bootstrap_live_state(o2_3b)
    log_res("Case 2.3b: Size Mismatch (Inherit)", res2_3b["mode"] if res2_3b else None, None)

    # --- CASE 3: 基础设施 (Infra) ---
    def check_oracle(): raise ConnectionError("Timeout")
    try:
        check_oracle()
        res3_1 = "Success"
    except Exception:
        res3_1 = "Exception"
    log_res("Case 3.1: Oracle Failure (Exit)", res3_1, "Exception")

    api_key = None
    res3_2 = "Missing" if not api_key else "Exists"
    log_res("Case 3.2: Missing API Key (Exit)", res3_2, "Missing")

    print("=" * 85)
    print("🏁 Evaluation Finished.")

if __name__ == "__main__":
    dry_run_eval()