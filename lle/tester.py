# tester.py
#
# Hardcoded feasibility scanner
# No CLI, no modification to existing system.

import math
import db
from algorithm_core import (
    _num,
    _poly6_value,
    calculateFIL,
    calculateFIV,
    calculateObjectiveFunction,
)

# ==============================
# Hardcoded test parameters
# ==============================

CCT = 4000
CRI = 80
TARGET_LUMINAIRE_EFFICACY_LIST = [180, 185, 190, 195, 200]
TARGET_LUMEN = 5000

OPTICAL = 100     # %
POWER = 100       # %
TJ = 65          # °C

IF_MIN = 0.001   # mA  (very small, to catch sub-1mA roots)
SCAN_POINTS = 600


# ==============================
# Helpers
# ==============================

def logspace(start, stop, n):
    a = math.log10(start)
    b = math.log10(stop)
    step = (b - a) / (n - 1)
    return [10 ** (a + i * step) for i in range(n)]


def derive_led_targets(target_lumen, target_efficacy):
    optical_rate = OPTICAL / 100.0
    power_rate = POWER / 100.0
    combined = optical_rate * power_rate

    target_led_lumen = target_lumen / optical_rate
    target_led_efficacy = target_efficacy / combined

    return target_led_lumen, target_led_efficacy


def scan_model(row, target_led_efficacy):
    model = row.get("Model")
    if_max = _num(row.get("If_max"), 0.0)
    lm_test = _num(row.get("lm_test"), 0.0)

    if if_max <= 0:
        return None

    try:
        lumen_factor = _num(_poly6_value(TJ, row, "FTL"), 1.0)
    except:
        lumen_factor = 1.0

    try:
        vf_factor = _num(_poly6_value(TJ, row, "FTV"), 1.0)
    except:
        vf_factor = 1.0

    k_phi = lm_test * lumen_factor
    k_eta = target_led_efficacy * vf_factor

    xs = logspace(IF_MIN, if_max, SCAN_POINTS)

    min_abs_f = float("inf")
    min_if = None
    min_f = None
    max_f = -1e30

    sign_change = False
    prev_f = None

    for x in xs:
        f = calculateObjectiveFunction(x, k_eta, k_phi, row)

        if prev_f is not None and f * prev_f <= 0:
            sign_change = True

        prev_f = f

        af = abs(f)
        if af < min_abs_f:
            min_abs_f = af
            min_if = x
            min_f = f

        if f > max_f:
            max_f = f

    return {
        "Model": model,
        "If_max": if_max,
        "lm_test": lm_test,
        "FTL": lumen_factor,
        "FTV": vf_factor,
        "sign_change": sign_change,
        "min_abs_f": min_abs_f,
        "if_at_min_abs_f": min_if,
        "f_at_min": min_f,
        "max_f": max_f,
    }


# ==============================
# Main
# ==============================

rows = db.fetch_candidates_by_cct_cri(CCT, CRI)
rows = [r for r in rows if "STW8A2PD" in r.get("Model", "")]

print("Models:", [r["Model"] for r in rows])
print("=" * 80)

for target_efficacy in TARGET_LUMINAIRE_EFFICACY_LIST:

    print("\n==============================")
    print("Target Luminaire Efficacy:", target_efficacy)
    print("==============================")

    _, target_led_efficacy = derive_led_targets(
        TARGET_LUMEN,
        target_efficacy
    )

    print("Target LED Efficacy (internal):", round(target_led_efficacy, 6))
    print()

    for r in rows:
        r["lm_test"] = _num(r.get("lm_test"), 0.0)
        result = scan_model(r, target_led_efficacy)

        if not result:
            continue

        print(
            result["Model"],
            "| sign_change:", result["sign_change"],
            "| min_abs_f:", round(result["min_abs_f"], 6),
            "| if@min:", round(result["if_at_min_abs_f"], 6),
        )

    print()
