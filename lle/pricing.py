# pricing.py
# Cost calculation and candidate ranking layer

from functools import cmp_to_key
from algorithm_core import _num


def _compare_cost_items(a, b):
    if a['cost'] == b['cost']:
        return 0
    return -1 if a['cost'] < b['cost'] else 1


def _candidate_cost_item(candidate_index, candidate, led_config_solutions, smt_cost_rmb, usd_rate):
    first_solution = led_config_solutions[candidate_index][0]
    total_leds = _num(first_solution.get('total_leds', 0), 0)
    unit_usd = _num(candidate.get('USD', 0), 0)

    led_cost_usd = total_leds * unit_usd if unit_usd > 0 else 0
    smt_cost_usd = total_leds * _num(smt_cost_rmb, 0) / max(_num(usd_rate, 1), 1e-9)

    total_cost_usd = led_cost_usd + smt_cost_usd

    return {
        'index': candidate_index,
        'cost': total_cost_usd,
        'candidate': candidate,
    }


def sorted_candidate_cost_items(led_candidates, led_config_solutions, smt_cost_rmb, usd_rate):
    items = []

    for i, c in enumerate(led_candidates):
        solutions = led_config_solutions.get(i)
        if solutions:
            items.append(
                _candidate_cost_item(
                    i,
                    c,
                    led_config_solutions,
                    smt_cost_rmb,
                    usd_rate
                )
            )

    return sorted(items, key=cmp_to_key(_compare_cost_items))
