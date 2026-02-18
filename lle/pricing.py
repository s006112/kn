# pricing.py
# Cost calculation and candidate ranking layer

from algorithm_core import _num


def _cost_rmb(*, total_leds: float, unit_rmb: float, smt_cost_rmb: float):
    led_cost_rmb = total_leds * unit_rmb if (total_leds > 0 and unit_rmb > 0) else 0.0
    smt_cost_rmb_total = total_leds * smt_cost_rmb if total_leds > 0 else 0.0
    return led_cost_rmb, smt_cost_rmb_total, (led_cost_rmb + smt_cost_rmb_total)


def solution_cost_breakdown(
    *,
    total_leds: float,
    unit_rmb: float,
    smt_cost_rmb: float,
    usd_rate: float,
):
    total_leds = _num(total_leds, 0)
    unit_rmb = _num(unit_rmb, 0)
    smt_cost_rmb = _num(smt_cost_rmb, 0)
    usd_rate = max(_num(usd_rate, 1), 1e-9)

    led_cost_rmb, smt_cost_rmb_total, total_cost_rmb = _cost_rmb(
        total_leds=total_leds,
        unit_rmb=unit_rmb,
        smt_cost_rmb=smt_cost_rmb,
    )
    led_cost_usd = led_cost_rmb / usd_rate if led_cost_rmb > 0 else 0.0
    smt_cost_usd = smt_cost_rmb_total / usd_rate if smt_cost_rmb_total > 0 else 0.0
    total_cost_usd = total_cost_rmb / usd_rate if total_cost_rmb > 0 else 0.0

    return {
        "total_cost_usd": total_cost_usd,
        "total_cost_rmb": total_cost_rmb,
        "led_cost_usd": led_cost_usd,
        "smt_cost_usd": smt_cost_usd,
        "led_cost_rmb": led_cost_rmb,
        "smt_cost_rmb_total": smt_cost_rmb_total,
    }


def _candidate_cost_item(candidate_index, candidate, led_config_solutions, smt_cost_rmb, usd_rate):
    first_solution = led_config_solutions[candidate_index][0]
    total_leds = _num(first_solution.get("total_leds", 0), 0)
    costs = solution_cost_breakdown(
        total_leds=total_leds,
        unit_rmb=float(candidate.get("RMB", 0) or 0.0),
        smt_cost_rmb=_num(smt_cost_rmb, 0),
        usd_rate=usd_rate,
    )

    return {
        "index": candidate_index,
        "candidate": candidate,
        "cost": float(costs.get("total_cost_rmb", 0) or 0.0),
        "total_leds": total_leds,
        **costs,
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


def build_presented_results(
    *,
    sorted_candidates,
    led_config_solutions,
    optical_rate,
    target_efficacy,
    smt_cost_rmb,
    usd_rate,
):
    sorted_candidates_display = []
    candidate_costs_display = []

    for item in sorted_candidates:
        idx = item["index"]
        cand = item["candidate"]
        total_leds = float(item.get("total_leds", 0) or 0.0)

        lm_per_led = float(cand.get("lumen_at_target_Tj_target_if", 0) or 0.0)
        fixture_lm = (
            lm_per_led * total_leds * optical_rate
            if (lm_per_led > 0 and total_leds > 0 and optical_rate > 0)
            else 0.0
        )
        input_power = (
            fixture_lm / target_efficacy
            if (fixture_lm > 0 and target_efficacy > 0)
            else 0.0
        )

        costs = solution_cost_breakdown(
            total_leds=total_leds,
            unit_rmb=float(cand.get("RMB", 0) or 0.0),
            smt_cost_rmb=smt_cost_rmb,
            usd_rate=usd_rate,
        )
        led_cost_usd = float(costs.get("led_cost_usd", 0) or 0.0)
        smt_cost_usd = float(costs.get("smt_cost_usd", 0) or 0.0)
        total_cost_usd = float(costs.get("total_cost_usd", 0) or 0.0)
        led_cost_rmb = float(costs.get("led_cost_rmb", 0) or 0.0)
        smt_cost_rmb_total = float(costs.get("smt_cost_rmb_total", 0) or 0.0)
        total_cost_rmb = float(costs.get("total_cost_rmb", 0) or 0.0)

        sorted_candidates_display.append(
            {
                "index": idx,
                "candidate": cand,
                "fixture_lm": fixture_lm,
                "input_power": input_power,
                "total_led_count": int(total_leds) if total_leds > 0 else None,
                "total_cost_usd": total_cost_usd
                if (led_cost_usd > 0 or smt_cost_usd > 0)
                else None,
                "total_cost_rmb": total_cost_rmb
                if (led_cost_rmb > 0 or smt_cost_rmb_total > 0)
                else None,
                "led_cost_usd": led_cost_usd,
                "smt_cost_usd": smt_cost_usd,
            }
        )

        solutions_display = []
        for sol in (led_config_solutions.get(idx) or [])[:10]:
            total_leds_sol = float(sol.get("total_leds", 0) or 0.0)

            target_if_ma = float(cand.get("target_if", 0) or 0.0)
            total_current = (
                target_if_ma * float(sol.get("P", 0) or 0.0)
                if target_if_ma > 0
                else 0.0
            )

            voltage = float(sol.get("V_chain", 0) or 0.0)
            power_watts = (
                voltage * total_current / 1000.0
                if (voltage > 0 and total_current > 0)
                else 0.0
            )

            costs = solution_cost_breakdown(
                total_leds=total_leds_sol,
                unit_rmb=float(cand.get("RMB", 0) or 0.0),
                smt_cost_rmb=smt_cost_rmb,
                usd_rate=usd_rate,
            )

            solutions_display.append(
                {
                    "solution": sol,
                    "total_current": total_current,
                    "power_watts": power_watts,
                    **costs,
                }
            )

        candidate_costs_display.append(
            {
                "index": idx,
                "candidate": cand,
                "solutions_display": solutions_display,
            }
        )

    return {
        "sorted_candidates_display": sorted_candidates_display,
        "candidate_costs_display": candidate_costs_display,
        "candidate_count": len(sorted_candidates),
    }
