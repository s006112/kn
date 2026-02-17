# topology.py
# Pure series-parallel configuration generator (no DB, no cost logic, no physics)

from algorithm_core import _num


def generate_config_solutions(required_led_count, vf_single, v_chain_max):
    """
    Generate feasible series-parallel LED configurations.

    Inputs:
        required_led_count: required LED quantity
        vf_single: forward voltage per LED (already computed)
        v_chain_max: maximum allowed chain voltage

    Returns:
        list of solution dict:
        {
            'P': parallel_count,
            'S': series_count,
            'led_add': added_leds,
            'V_chain': chain_voltage,
            'total_leds': total_leds,
        }
    """

    solutions = []

    if required_led_count <= 0 or vf_single <= 0:
        return solutions

    v_chain_max_value = _num(v_chain_max, 50)

    P = 1
    solution_index = 0
    max_parallel = min(20, required_led_count)

    while P <= max_parallel and solution_index < 10:

        led_count_working = required_led_count
        led_add = 0

        while (led_count_working % P) != 0:
            led_count_working += 1
            led_add += 1

        S = led_count_working // P

        if S >= 2:
            V_chain = S * vf_single

            if V_chain <= v_chain_max_value:
                solutions.append({
                    'P': P,
                    'S': S,
                    'led_add': led_add,
                    'V_chain': V_chain,
                    'total_leds': led_count_working,
                })
                solution_index += 1

        P += 1

    return solutions
