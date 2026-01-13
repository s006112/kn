from __future__ import annotations

# Defines pricing Inputs/Params data structures and price_quote/validate_inputs logic.
# Called by services.build_quote_context (triggered from app_q routes) to compute quote summaries.

import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass
class Inputs:
    layers: int
    pp_cost: float
    inner_cost: float
    stacking_cost: float
    panel_boards: int
    stack_qty: int
    pcb_thickness: str
    cnc_hole_dimension: str
    cnc_pth_holes: int
    material: str
    substrate_thickness: str
    cu_thickness: str
    finish: str
    plating: str
    etching_cost: float
    masking: str
    silkscreen_cost: float
    routing_length: float
    stamping_cost: float
    post_process_cost: float
    sewage_water: float
    sewage_electricity: float


@dataclass
class Params:
    material_costs: dict[str, dict[str, dict[str, float]]]
    finish_costs: dict[str, float]
    masking_costs: dict[str, float]
    plating_costs: dict[str, float]
    labor_cost: float
    loss_pct: float
    margin_pct: float
    cnc_pth_per_hole: float
    routing_per_inch: float


def _non_negative(value: float) -> float:
    return max(value, 0.0)


def _percent(value: float) -> float:
    return max(0.0, min(100.0, value))


def _component_total(components: Mapping[str, float]) -> float:
    return sum(components.values())


def _rounded(components: Mapping[str, float], digits: int) -> dict[str, float]:
    return {name: round(amount, digits) for name, amount in components.items()}


def _component_section(total: float, components: Mapping[str, float], digits: int) -> dict[str, Any]:
    return {"total": round(total, digits), "components": _rounded(components, digits)}


def price_quote(inp: Inputs, prm: Params) -> dict[str, Any]:
    boards_per_panel = max(1, int(inp.panel_boards) if inp.panel_boards else 1)
    stack_qty = max(1, int(inp.stack_qty) if inp.stack_qty else 1)

    laminate_cost = 15.0
    material_map = prm.material_costs.get(inp.material)
    if isinstance(material_map, dict):
        substrate_map = material_map.get(inp.substrate_thickness)
        if isinstance(substrate_map, dict):
            laminate_cost = substrate_map.get(inp.cu_thickness, laminate_cost)
    material_components = {
        "laminate": laminate_cost,
    }

    pp_base = _non_negative(inp.pp_cost)
    inner_base = _non_negative(inp.inner_cost)
    stacking_base = _non_negative(inp.stacking_cost)
    if inp.layers >= 3:
        material_multiplier = math.ceil(max( inp.layers / 2.0 - 1, 0.0))
        pp_multiplier = math.ceil(max(inp.layers / 2.0, 0.0))
        inner_multiplier = math.ceil(max(inp.layers - 2.0, 0.0))
        stacking_component = stacking_base
    else:
        material_multiplier = 1.0
        pp_multiplier = 0.0
        inner_multiplier = 0.0
        stacking_component = 0.0
    material_cost = _component_total(material_components) * material_multiplier
    multi_layer_components = {
        "pp_cost": pp_base * pp_multiplier,
        "inner_cost": inner_base * inner_multiplier,
        "stacking_cost": stacking_component,
    }
    multi_layer_cost = _component_total(multi_layer_components)
    multi_layer_section = _component_section(multi_layer_cost, multi_layer_components, 2)
    multi_layer_section["details"] = {
        "material_multiplier": round(material_multiplier, 3),
        "pp_multiplier": round(pp_multiplier, 3),
        "inner_multiplier": round(inner_multiplier, 3),
        "layers": inp.layers,
        "layers_lt_three": inp.layers < 3,
    }
    multi_layer_section["raw"] = {
        "pp_cost": pp_base,
        "inner_cost": inner_base,
        "stacking_cost": stacking_base,
    }

    treatment_components = {
        "finish": prm.finish_costs.get(inp.finish, 0.0),
        "etching": inp.etching_cost,
        "masking": prm.masking_costs.get(inp.masking, 0.0),
        "silkscreen": inp.silkscreen_cost,
    }
    treatment_cost = _component_total(treatment_components)

    cnc_rate = _non_negative(prm.cnc_pth_per_hole)
    cnc_holes_single = max(0, int(inp.cnc_pth_holes))
    cnc_holes_panel = cnc_holes_single * boards_per_panel
    cnc_components = {
        "cnc_pth": cnc_rate * cnc_holes_panel,
    }
    cnc_stack_cost = _component_total(cnc_components)
    cnd_cost_panel = cnc_stack_cost / stack_qty if stack_qty else cnc_stack_cost
    cnc_cost = cnd_cost_panel

    routing_length_single = _non_negative(inp.routing_length)
    routing_length_panel = routing_length_single * boards_per_panel
    routing_rate = _non_negative(prm.routing_per_inch)
    process_components = {
        "plating": prm.plating_costs.get(inp.plating, 0.0),
        "routing": routing_length_panel * routing_rate,
        "stamping": _non_negative(inp.stamping_cost),
        "post_process": _non_negative(inp.post_process_cost),
    }
    process_cost = _component_total(process_components)

    labor_cost = _non_negative(prm.labor_cost)
    overhead_components = {
        "water": _non_negative(inp.sewage_water),
        "electricity": _non_negative(inp.sewage_electricity),
        "labor": labor_cost,
    }
    overhead_cost = _component_total(overhead_components)

    base = (
        material_cost
        + multi_layer_cost
        + treatment_cost
        + cnc_cost
        + process_cost
        + overhead_cost
    )

    loss_pct = _percent(prm.loss_pct)
    cogs = base * (1 + loss_pct / 100.0)
    loss_cost = cogs - base

    cogs_unit = cogs / boards_per_panel if boards_per_panel else 0.0
    margin_pct = _non_negative(prm.margin_pct)
    price_unit = cogs_unit * (1 + margin_pct / 100.0)
    margin_cost = cogs * margin_pct / 100.0

    overhead_section = _component_section(overhead_cost, overhead_components, 2)
    loss_rounded = round(loss_cost, 2)
    rounded_others = {
        "total": loss_rounded,
        "overhead": overhead_section["total"],
        "loss": loss_rounded,
        "margin": round(margin_cost, 2),
    }

    breakdown = {
        "material": _component_section(material_cost, material_components, 2),
        "multi_layer": multi_layer_section,
        "treatment": _component_section(treatment_cost, treatment_components, 2),
        "cnc": {
            "total": round(cnc_cost, 1),
            "cnd_cost_panel": round(cnd_cost_panel, 1),
            "stack_qty": stack_qty,
            "cnc_holes_single": cnc_holes_single,
            "cnc_holes_panel": cnc_holes_panel,
            "components": _rounded(cnc_components, 1),
        },
        "process": _component_section(process_cost, process_components, 1),
        "routing_length_single": round(routing_length_single, 4),
        "routing_length_panel": round(routing_length_panel, 4),
        "overhead": overhead_section,
        "others": rounded_others,
        "boards_per_panel": boards_per_panel,
    }
    return {
        "cogs": round(cogs, 2),
        "cogs_unit": round(cogs_unit, 4),
        "price_unit": round(price_unit, 4),
        "breakdown": breakdown,
    }


def validate_inputs(data: Mapping[str, Any]) -> list[str]:
    errs: list[str] = []
    layers = data.get("layers", 0)
    if not (1 <= layers <= 40):
        errs.append("Layers must be 1–40.")
    if data.get("panel_boards", 0) < 1:
        errs.append("Boards per panel must be >= 1.")
    if data.get("stack_qty", 1) < 1:
        errs.append("Stack quantity must be >= 1.")
    if data.get("cnc_pth_holes", 0) < 0:
        errs.append("CNC PTH holes must be >= 0.")
    if data.get("routing_length", 0.0) < 0:
        errs.append("Routing length must be >= 0.")
    if data.get("stamping_cost", 0.0) < 0:
        errs.append("Stamping cost must be >= 0.")
    if data.get("post_process_cost", 0.0) < 0:
        errs.append("Post Process cost must be >= 0.")
    if data.get("labor_cost", 0.0) < 0:
        errs.append("Labor cost must be >= 0.")
    if data.get("pp_cost", 0.0) < 0:
        errs.append("PP cost must be >= 0.")
    if data.get("inner_cost", 0.0) < 0:
        errs.append("Inner cost must be >= 0.")
    if data.get("stacking_cost", 0.0) < 0:
        errs.append("Stacking cost must be >= 0.")
    return errs
