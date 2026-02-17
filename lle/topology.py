# topology.py
# Pure series-parallel configuration generator (no DB, no cost logic, no physics)

from algorithm_core import _num


def generate_config_solutions(required_led_count, vf_single, v_chain_max, max_solutions=10):
    """
    Generate feasible series-parallel LED configurations
    without arbitrary parallel limit.
    """

    solutions = []

    if required_led_count <= 0 or vf_single <= 0:
        return solutions

    v_chain_max_value = _num(v_chain_max, 50)

    # ---- Step 1: Compute maximum allowed series count ----
    S_max = int(v_chain_max_value // vf_single)

    if S_max < 2:
        return solutions  # physically impossible

    # ---- Step 2: Compute minimal parallel count ----
    import math
    P_min = math.ceil(required_led_count / S_max)

    # ---- Step 3: Search feasible P starting from P_min ----
    P = P_min

    while len(solutions) < max_solutions:

        # compute minimal total LEDs for this P
        S = math.ceil(required_led_count / P)

        if S > S_max:
            P += 1
            continue

        total_leds = P * S
        led_add = total_leds - required_led_count
        V_chain = S * vf_single

        solutions.append({
            'P': P,
            'S': S,
            'led_add': led_add,
            'V_chain': V_chain,
            'total_leds': total_leds,
        })

        P += 1

        # 安全停止条件（避免无限增长）
        if P > required_led_count:
            break

    return solutions
