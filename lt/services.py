from __future__ import annotations

# Service layer shared by app_q routes, orchestrating pricing/panelizer builders and PresetsStore hooks.
# Called by app_q index/panelizer_only endpoints; internally calls pricing.price_quote and panelizer helpers.

from copy import deepcopy
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, NamedTuple, Sequence

from pricing import Inputs, Params, price_quote, validate_inputs


class PanelizerState(NamedTuple):
    config: Dict[str, Any]
    rows: List[Dict[str, Any]]
    summary: Dict[str, Any] | None
    error: str | None


def _parse_float_field(form: Mapping[str, Any], name: str, default: float) -> float:
    raw = form.get(name)
    raw_str = str(default) if raw in (None, "") else str(raw).strip()
    try:
        return float(raw_str)
    except ValueError:
        raise ValueError(f"{name} must be a number")


def _parse_int_field(form: Mapping[str, Any], name: str, default: int) -> int:
    raw = form.get(name)
    raw_str = str(default) if raw in (None, "") else str(raw).strip()
    try:
        return int(raw_str)
    except ValueError:
        raise ValueError(f"{name} must be an integer")


def _text_field(form: Mapping[str, Any], name: str, default: Any) -> str:
    raw = form.get(name)
    return str(default) if raw is None else str(raw)


def make_inputs(
    form: Mapping[str, Any],
    *,
    defaults: Mapping[str, Any],
    input_type_hints: Mapping[str, Any],
    stack_qty_lookup: Callable[[str | None, str | None], int | None],
) -> Inputs:
    payload: Dict[str, Any] = {}
    for name, hint in input_type_hints.items():
        default = defaults[name]
        if hint is int:
            payload[name] = _parse_int_field(form, name, int(default))
        elif hint is float:
            payload[name] = _parse_float_field(form, name, float(default))
        else:
            payload[name] = _text_field(form, name, default)
    derived_stack_qty = stack_qty_lookup(
        payload.get("pcb_thickness"),
        payload.get("cnc_hole_dimension"),
    )
    payload["stack_qty"] = derived_stack_qty if derived_stack_qty is not None else max(1, int(payload.get("stack_qty", 1)))
    return Inputs(**payload)


def make_params(
    form: Mapping[str, Any],
    *,
    defaults: Mapping[str, Any],
    param_type_hints: Mapping[str, Any],
    priced_fields: Sequence[Any],
) -> Params:
    payload: Dict[str, Any] = {}
    for name, hint in param_type_hints.items():
        default = defaults.get(name)
        if hint is float and default is not None:
            payload[name] = _parse_float_field(form, name, float(default))
        elif hint is int and default is not None:
            payload[name] = _parse_int_field(form, name, int(default))
        else:
            value = deepcopy(default) if isinstance(default, dict) else default
            payload[name] = value

    selected_choices = {
        field.name: _text_field(form, field.name, defaults.get(field.name, ""))
        for field in priced_fields
    }

    def _apply_override(price_field: str, map_key: str, selected: str | None, err_msg: str) -> None:
        raw = form.get(price_field)
        if raw in (None, ""):
            return
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(err_msg)
        if not selected:
            return
        price_map = payload.get(map_key)
        if price_map is None:
            price_map = {}
            payload[map_key] = price_map
        keys = [selected]
        if map_key == "material_costs":
            substrate = form.get("substrate_thickness") or defaults.get("substrate_thickness")
            cu = form.get("cu_thickness") or defaults.get("cu_thickness")
            if substrate:
                keys.append(str(substrate))
            if cu:
                keys.append(str(cu))

        current: MutableMapping[str, Any] = price_map
        for key in keys[:-1]:
            next_level = current.get(key)
            if not isinstance(next_level, dict):
                next_level = {}
                current[key] = next_level
            current = next_level
        current[keys[-1]] = value

    for field in priced_fields:
        _apply_override(field.price_field, field.map_key, selected_choices.get(field.name), field.error)
    return Params(**payload)


def resolve_panelizer_state(
    values: Mapping[str, Any],
    *,
    panelizer_config_fn: Callable[[Mapping[str, Any]], Dict[str, Any]],
    panelizer_rows_fn: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
    panelizer_summary_fn: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
    panelizer_default_config_fn: Callable[[], Dict[str, Any]],
) -> PanelizerState:
    cfg = panelizer_default_config_fn()
    rows: List[Dict[str, Any]] = []
    summary: Dict[str, Any] | None = None
    error: str | None = None
    try:
        cfg = panelizer_config_fn(values)
        rows = panelizer_rows_fn(cfg)
        summary = panelizer_summary_fn(rows, cfg)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    return PanelizerState(cfg, rows, summary, error)


def _form_price_value(
    form: Mapping[str, Any],
    *,
    field_name: str,
    defaults_map: Mapping[str, Any],
    selected: str,
    values_map: Mapping[str, str],
    defaults: Mapping[str, Any],
) -> str:
    raw = form.get(field_name)
    if raw not in (None, ""):
        return str(raw)
    default_value: Any = defaults_map.get(selected)
    if field_name == "material_price" and isinstance(defaults_map, dict):
        nested = defaults_map.get(selected)
        if isinstance(nested, dict):
            substrate = values_map.get("substrate_thickness") or defaults.get("substrate_thickness")
            cu = values_map.get("cu_thickness") or defaults.get("cu_thickness")
            if substrate and cu:
                default_value = nested.get(substrate, {}).get(cu)
    return "" if default_value in (None, "") else str(default_value)


def build_quote_context(
    *,
    form: Mapping[str, Any],
    values: Mapping[str, Any],
    method: str,
    defaults: Mapping[str, Any],
    input_type_hints: Mapping[str, Any],
    param_type_hints: Mapping[str, Any],
    input_field_names: Sequence[str],
    priced_fields: Sequence[Any],
    priced_default_maps: Mapping[str, Dict[str, Any]],
    select_options: Mapping[str, Sequence[str]],
    pcb_thickness_options: Sequence[str],
    cnc_hole_dimension_options: Sequence[str],
    substrate_thickness_options: Sequence[str],
    cu_thickness_options: Sequence[str],
    stack_qty_lookup: Callable[[str | None, str | None], int | None],
    stack_qty_map: Mapping[str, Any],
    priced_client_config: Sequence[Dict[str, str]],
    persist_defaults: Callable[[Inputs, Params, Dict[str, Any] | None], None],
    panelizer_config_fn: Callable[[Mapping[str, Any]], Dict[str, Any]],
    panelizer_rows_fn: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
    panelizer_summary_fn: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
    panelizer_form_defaults_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    panelizer_default_config_fn: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    panelizer_state = resolve_panelizer_state(
        values,
        panelizer_config_fn=panelizer_config_fn,
        panelizer_rows_fn=panelizer_rows_fn,
        panelizer_summary_fn=panelizer_summary_fn,
        panelizer_default_config_fn=panelizer_default_config_fn,
    )
    panelizer_cfg = panelizer_state.config
    panelizer_rows = panelizer_state.rows
    panelizer_summary = panelizer_state.summary
    panelizer_error = panelizer_state.error

    computed_panel_boards: int | None = None
    if panelizer_summary:
        max_pcbs = panelizer_summary.get("max_pcbs_per_jumbo")
        if max_pcbs is not None:
            computed_panel_boards = max(1, int(max_pcbs))

    error_msgs: List[str] = []
    result: Dict[str, Any] | None = None
    resolved_inputs: Inputs | None = None
    persist_defaults_requested = form.get("persist_defaults") == "1"

    if method == "POST":
        try:
            inp = make_inputs(
                form,
                defaults=defaults,
                input_type_hints=input_type_hints,
                stack_qty_lookup=stack_qty_lookup,
            )
            if computed_panel_boards is not None:
                inp.panel_boards = computed_panel_boards
            resolved_inputs = inp
            errs = validate_inputs(vars(inp))
            if errs:
                error_msgs = errs
            else:
                prm = make_params(
                    form,
                    defaults=defaults,
                    param_type_hints=param_type_hints,
                    priced_fields=priced_fields,
                )
                result = price_quote(inp, prm)
                persist_panelizer = None if panelizer_error else panelizer_cfg
                if persist_defaults_requested:
                    persist_defaults(inp, prm, persist_panelizer)
        except Exception as exc:  # noqa: BLE001
            error_msgs = [str(exc)]
            result = None

    param_defaults = {
        name: defaults[name]
        for name, hint in param_type_hints.items()
        if hint in (int, float) and name in defaults
    }
    form_defaults = {name: defaults[name] for name in input_field_names if name in defaults}
    form_values = {name: _text_field(form, name, form_defaults[name]) for name in form_defaults}
    if resolved_inputs is not None:
        form_values["stack_qty"] = str(resolved_inputs.stack_qty)
        form_values["panel_boards"] = str(resolved_inputs.panel_boards)
    param_values = {name: _text_field(form, name, param_defaults[name]) for name in param_defaults}
    panelizer_form_defaults = panelizer_form_defaults_fn(panelizer_default_config_fn())

    selected_choices = {
        field.name: form_values.get(field.name, str(defaults.get(field.name, "")))
        for field in priced_fields
    }

    price_value_kwargs = {}
    for field in priced_fields:
        defaults_map = priced_default_maps[field.name]
        price_value_kwargs[f"{field.price_field}_value"] = _form_price_value(
            form,
            field_name=field.price_field,
            defaults_map=defaults_map,
            selected=selected_choices[field.name],
            values_map=form_values,
            defaults=defaults,
        )

    if resolved_inputs is None and computed_panel_boards is not None:
        form_values["panel_boards"] = str(computed_panel_boards)

    template_defaults = form_defaults.copy()
    template_defaults.update(param_defaults)

    context = {
        "defaults": template_defaults,
        "values": form_values,
        "params_defaults": param_defaults,
        "params_values": param_values,
        "pcb_thickness_options": pcb_thickness_options,
        "cnc_hole_dimension_options": cnc_hole_dimension_options,
        "substrate_thickness_options": substrate_thickness_options,
        "cu_thickness_options": cu_thickness_options,
        "error_msgs": error_msgs,
        "result": result,
        "priced_fields": priced_fields,
        "priced_options": select_options,
        "priced_costs": priced_default_maps,
        "priced_client_config": priced_client_config,
        "stack_qty_map": stack_qty_map,
        "panelizer_values": panelizer_cfg,
        "panelizer_defaults": panelizer_form_defaults,
        "panelizer_summary": panelizer_summary,
        "panelizer_rows": panelizer_rows,
        "panelizer_error": panelizer_error,
        "panelizer_only": False,
    }
    context.update(price_value_kwargs)
    return context


def build_panelizer_only_context(
    *,
    values: Mapping[str, Any],
    priced_fields: Sequence[Any],
    select_options: Mapping[str, Sequence[str]],
    priced_default_maps: Mapping[str, Dict[str, Any]],
    stack_qty_map: Mapping[str, Any],
    priced_client_config: Sequence[Dict[str, str]],
    panelizer_config_fn: Callable[[Mapping[str, Any]], Dict[str, Any]],
    panelizer_rows_fn: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
    panelizer_summary_fn: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
    panelizer_form_defaults_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    panelizer_default_config_fn: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    panelizer_state = resolve_panelizer_state(
        values,
        panelizer_config_fn=panelizer_config_fn,
        panelizer_rows_fn=panelizer_rows_fn,
        panelizer_summary_fn=panelizer_summary_fn,
        panelizer_default_config_fn=panelizer_default_config_fn,
    )
    panelizer_form_defaults = panelizer_form_defaults_fn(panelizer_default_config_fn())
    return {
        "defaults": {},
        "values": {},
        "params_defaults": {},
        "params_values": {},
        "error_msgs": [],
        "result": None,
        "priced_fields": priced_fields,
        "priced_options": select_options,
        "priced_costs": priced_default_maps,
        "priced_client_config": priced_client_config,
        "stack_qty_map": stack_qty_map,
        "panelizer_values": panelizer_state.config,
        "panelizer_defaults": panelizer_form_defaults,
        "panelizer_summary": panelizer_state.summary,
        "panelizer_rows": panelizer_state.rows,
        "panelizer_error": panelizer_state.error,
        "panelizer_only": True,
    }
