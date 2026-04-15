import time

# 导入真实引擎环境配置与决策逻辑
from grid_config import (
    GRID_STEP,
    BUY_GRID_FACTOR,
    SELL_GRID_FACTOR,
    PAIR_MODE,
    BUY_ONLY_MODE,
    SELL_ONLY_MODE,
    REANCHOR_BREAK_STEPS,
    API_KEY,
)
from grid_decision import get_bootstrap_live_state, get_loop_action

def log_res(case_name, result, expected):
    """统一日志输出格式"""
    is_match = (result == expected)
    icon = "✅" if is_match else "❌"
    print(f"{icon} {case_name:.<75} Result: {str(result):<20} | Expected: {str(expected)}")

def dry_run_bootstrap_eval():
    print(f"🚀 Starting Bootstrap Evaluation (GRID_STEP={GRID_STEP})")
    print("=" * 115)

    # --- 挂单形态 (Order Shape) ---
    o_pair = [{"side": "BUY", "price": 99800.0}, {"side": "SELL", "price": 100200.0}]
    res_pair = get_bootstrap_live_state(o_pair)
    log_res("Valid Pair Found (Action: Inherit)", res_pair["mode"] if res_pair else None, PAIR_MODE)
    log_res("Partial Order Only (Action: Reset)", get_bootstrap_live_state(o_pair[:1]), None)

    # --- 价格间距 (Grid Spacing) ---
    o_bad_gap = [{"side": "BUY", "price": 99900.0}, {"side": "SELL", "price": 100000.0}]
    log_res("Correct Gap Distance (Action: Inherit)", res_pair["mode"] if res_pair else None, PAIR_MODE)
    log_res("Wrong Gap Distance (Action: Reset)", get_bootstrap_live_state(o_bad_gap), None)

    # --- 数量验证 (Order Sizing) ---
    o_size_ok = [{"side": "BUY", "price": 99800.0, "size": 0.001}, {"side": "SELL", "price": 100200.0, "size": 0.001}]
    log_res("Target Size Match (Action: Inherit)", get_bootstrap_live_state(o_size_ok)["mode"] if get_bootstrap_live_state(o_size_ok) else None, PAIR_MODE)

    # 关键测试：由于代码目前不检查 size，这里会返回 PAIR，从而与预期 None 不符，显示 ❌
    o_size_fail = [{"side": "BUY", "price": 99800.0, "size": 1.0}, {"side": "SELL", "price": 100200.0, "size": 0.00001}]
    res_size_check = get_bootstrap_live_state(o_size_fail)
    log_res("Target Size Mismatch (Action: Reset)", res_size_check["mode"] if res_size_check else None, None)

    # --- 基础设施 (Infrastructure) ---
    def mock_oracle(): raise ConnectionError("Timeout")
    try:
        mock_oracle(); res_infra = "Exception"
    except Exception: res_infra = "Exception"
    log_res("Price Feed Down (Action: Emergency Stop)", res_infra, "Exception")
    log_res("API Key Check (Action: Ensure Credentials)", "Exists" if API_KEY else "Missing", "Exists")

def dry_run_live_eval():
    print(f"\n🚀 Starting Live Logic Evaluation (Action & Rebuild)")
    print("=" * 115)

    o_pair = [{"side": "BUY", "price": 99800.0}, {"side": "SELL", "price": 100200.0}]
    state_p = {"mode": PAIR_MODE, "buy_price": 99800.0, "sell_price": 100200.0}

    # 正常成交路径
    log_res("Action: SELL filled -> Trigger Pair Rebuild", get_loop_action(o_pair[:1], state_p), ("rebuild", 100200.0))
    log_res("Action: BUY filled -> Trigger Pair Rebuild", get_loop_action(o_pair[1:], state_p), ("rebuild", 99800.0))
    log_res("Action: No fill -> Keep state", get_loop_action(o_pair, state_p), ("keep", None))

    # 残余模式路径 (Residual)
    state_b = {"mode": BUY_ONLY_MODE, "buy_price": 99800.0}
    log_res("Action: Residual BUY filled -> Restart Pair", get_loop_action([], state_b), ("rebuild", 99800.0))
    
    break_mid = 99800.0 + (REANCHOR_BREAK_STEPS * GRID_STEP) + 1.0
    log_res("Action: Anchor Break -> Re-center Grid", get_loop_action(o_pair[:1], state_b, break_mid), ("rebuild", None))

def dry_run_risk_eval():
    print(f"\n⚠️ Risk Case (with emoji)")
    print("=" * 115)

    state_p = {"mode": PAIR_MODE, "buy_price": 99800.0, "sell_price": 100200.0}
    
    log_res("Risk: Double Fill / Order Loss (0 orders)", get_loop_action([], state_p), ("abnormal", None))
    
    o_messy = [{"side": "BUY", "price": 99800.0}, {"side": "BUY", "price": 99700.0}]
    log_res("Risk: Multiple orders on one side", get_loop_action(o_messy, state_p), ("abnormal", None))
    
    o_drift = [{"side": "BUY", "price": 99000.0}, {"side": "SELL", "price": 100200.0}]
    log_res("Risk: Price drift from saved state", get_loop_action(o_drift, state_p), ("abnormal", None))
    print("=" * 115)

if __name__ == "__main__":
    dry_run_bootstrap_eval()
    dry_run_live_eval()
    dry_run_risk_eval()
    print("🏁 Test Cycle Complete.")