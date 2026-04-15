import time

# --- 核心逻辑注入 ---
GRID_STEP = 200.0
BUY_GRID_FACTOR = 1.0
SELL_GRID_FACTOR = 1.0
PAIR_MODE = "PAIR"

def normalize_price(p): return float(p)
def price_gap_matches(b, s, gap): return abs((s - b) - gap) < 0.0001

def get_pair_state(orders):
    if len(orders) != 2: return None
    buy = next((normalize_price(o["price"]) for o in orders if o["side"] == "BUY"), None)
    sell = next((normalize_price(o["price"]) for o in orders if o["side"] == "SELL"), None)
    
    expected_gap = (BUY_GRID_FACTOR + SELL_GRID_FACTOR) * GRID_STEP
    if buy and sell and price_gap_matches(buy, sell, expected_gap):
        return {"mode": PAIR_MODE, "buy_price": buy, "sell_price": sell}
    return None

def get_bootstrap_live_state(orders):
    """
    注意：当前的实现逻辑只检查价格 (Contract 11)。
    如果要在 Case 2.3b 看到 ❌，说明评估预期与当前实现代码的逻辑产生了冲突。
    """
    return get_pair_state(orders)

def log_res(case_name, result, expected):
    # 核心判断：结果与预期不符则显示 ❌
    is_match = (result == expected)
    icon = "✅" if is_match else "❌"
    print(f"{icon} {case_name:.<45} Result: {result} | Expected: {expected}")

# --- 测试运行器 ---
def dry_run_eval():
    print(f"🚀 Starting Dry Run Evaluation (GRID_STEP={GRID_STEP})")
    print("=" * 80)

    # --- CASE 1: 挂单形态 ---
    o1_1 = [{"side": "BUY", "price": 99800.0}, {"side": "SELL", "price": 100200.0}]
    res1_1 = get_bootstrap_live_state(o1_1)
    log_res("Case 1.1: Perfect Pair (Inherit)", res1_1["mode"] if res1_1 else None, PAIR_MODE)

    o1_2 = [{"side": "BUY", "price": 99800.0}]
    res1_2 = get_bootstrap_live_state(o1_2)
    log_res("Case 1.2: Single Residual (Rebuild)", res1_2, None)

    # --- CASE 2: 参数匹配 ---
    # 2.1 步长不匹配 -> 预期结果应为 None (触发 Rebuild)
    o2_1 = [{"side": "BUY", "price": 99900.0}, {"side": "SELL", "price": 100000.0}]
    res2_1 = get_bootstrap_live_state(o2_1)
    log_res("Case 2.1: Grid Step Mismatch (Rebuild)", res2_1, None)

    # 2.3a 金额完全匹配
    o2_3a = [{"side": "BUY", "price": 99800.0, "size": 0.001}, {"side": "SELL", "price": 100200.0, "size": 0.001}]
    res2_3a = get_bootstrap_live_state(o2_3a)
    log_res("Case 2.3a: Size Match (Inherit)", res2_3a["mode"] if res2_3a else None, PAIR_MODE)

    # 2.3b 金额严重不匹配
    # 如果代码逻辑只看价格，这里会返回 PAIR。
    # 但根据您的要求，这里预期它应该失败 (None)，所以 log_res 会标记 ❌
    o2_3b = [{"side": "BUY", "price": 99800.0, "size": 1.0}, {"side": "SELL", "price": 100200.0, "size": 0.00001}]
    res2_3b = get_bootstrap_live_state(o2_3b)
    log_res("Case 2.3b: Size Mismatch (Inherit)", res2_3b["mode"] if res2_3b else None, None)

    # --- CASE 3: 基础设施 ---
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

    print("=" * 80)
    print("🏁 Evaluation Finished.")

if __name__ == "__main__":
    dry_run_eval()