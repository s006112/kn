# pricing.py
# Cost calculation and candidate ranking layer

from algorithm_core import _num


def _cost_usd(*, total_leds: float, unit_usd: float, smt_cost_rmb: float, usd_rate: float):
    led_cost_usd = total_leds * unit_usd if (total_leds > 0 and unit_usd > 0) else 0.0
    smt_cost_usd = total_leds * smt_cost_rmb / max(usd_rate, 1e-9) if total_leds > 0 else 0.0
    return led_cost_usd, smt_cost_usd, (led_cost_usd + smt_cost_usd)


def _cost_rmb(*, total_leds: float, unit_rmb: float, smt_cost_rmb: float):
    led_cost_rmb = total_leds * unit_rmb if (total_leds > 0 and unit_rmb > 0) else 0.0
    smt_cost_rmb_total = total_leds * smt_cost_rmb if total_leds > 0 else 0.0
    return led_cost_rmb, smt_cost_rmb_total, (led_cost_rmb + smt_cost_rmb_total)


def _candidate_cost_item(candidate_index, candidate, led_config_solutions, smt_cost_rmb, usd_rate):
    first_solution = led_config_solutions[candidate_index][0]
    total_leds = _num(first_solution.get("total_leds", 0), 0)
    unit_usd = _num(candidate.get("USD", 0), 0)

    led_cost_usd, smt_cost_usd, total_cost_usd = _cost_usd(
        total_leds=total_leds,
        unit_usd=unit_usd,
        smt_cost_rmb=_num(smt_cost_rmb, 0),
        usd_rate=max(_num(usd_rate, 1), 1e-9),
    )

    return {
        "index": candidate_index,
        "candidate": candidate,
        "cost": total_cost_usd,
        "total_leds": total_leds,
        "led_cost_usd": led_cost_usd,
        "smt_cost_usd": smt_cost_usd,
        "total_cost_usd": total_cost_usd,
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
                    usd_rate,
                )
            )

    return sorted(items, key=lambda x: x["cost"])
