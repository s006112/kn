from __future__ import annotations

# Main Flask entrypoint wiring PresetsStore, pricing, panelizer, and services layers.
# Routes call into services.build_quote_context/build_panelizer_only_context which in turn invoke pricing/panelizer logic.

import os
import threading
from dataclasses import fields
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, get_type_hints

from flask import Flask, render_template, request, send_file

from config import PresetsStore
from pricing import Inputs, Params
from panelizer import (
    PANELIZER_CONFIG_KEYS,
    build_panelizer_config,
    compute_panelizer_rows,
    summarize_panelizer_results,
)
from services import build_panelizer_only_context, build_quote_context

app = Flask(__name__)
panelizer_app = Flask(
    __name__,
    template_folder=app.template_folder,
    static_folder=app.static_folder,
    static_url_path=app.static_url_path,
)
panelizer_app.config.update(app.config)
_panelizer_server_started = False
_panelizer_server_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Configuration & preset loading
# ---------------------------------------------------------------------------

ICON_FILENAME = "lt.png"
ICON_PATH = os.path.join(os.path.dirname(__file__), ICON_FILENAME)
presets_store = PresetsStore()
DEFAULTS = presets_store.defaults
INPUT_TYPE_HINTS = get_type_hints(Inputs)
PARAM_TYPE_HINTS = get_type_hints(Params)
INPUT_FIELD_NAMES = tuple(f.name for f in fields(Inputs))


# ---------------------------------------------------------------------------
# Data structures & default-derived options
# ---------------------------------------------------------------------------

class PricedField(NamedTuple):
    name: str
    price_field: str
    map_key: str
    error: str


PRICED_FIELDS: tuple[PricedField, ...] = (
    PricedField("material", "material_price", "material_costs", "Material price must be a number"),
    PricedField("finish", "finish_price", "finish_costs", "Finish cost must be a number"),
    PricedField("masking", "masking_price", "masking_costs", "Masking cost must be a number"),
    PricedField("plating", "plating_price", "plating_costs", "Plating cost must be a number"),
)

PRICED_CLIENT_CONFIG = [{"name": field.name, "priceField": field.price_field} for field in PRICED_FIELDS]

PRICED_DEFAULT_MAPS: dict[str, dict[str, Any]] = {}
SELECT_OPTIONS: dict[str, tuple[str, ...]] = {}
PCB_THICKNESS_OPTIONS: tuple[str, ...] = tuple()
CNC_HOLE_DIMENSION_OPTIONS: tuple[str, ...] = tuple()
SUBSTRATE_THICKNESS_OPTIONS: tuple[str, ...] = tuple()
CU_THICKNESS_OPTIONS: tuple[str, ...] = tuple()
PANELIZER_PANEL_OPTIONS: dict[str, tuple[float, float]] = {}
PANELIZER_JUMBO_MULTIPLIER: dict[str, int] = {}


def _refresh_cached_defaults() -> None:
    global DEFAULTS, PCB_THICKNESS_OPTIONS, CNC_HOLE_DIMENSION_OPTIONS
    global SUBSTRATE_THICKNESS_OPTIONS, CU_THICKNESS_OPTIONS
    global PANELIZER_PANEL_OPTIONS, PANELIZER_JUMBO_MULTIPLIER
    DEFAULTS = presets_store.defaults
    for field in PRICED_FIELDS:
        defaults_map = presets_store.defaults_map(field.map_key)
        PRICED_DEFAULT_MAPS[field.name] = defaults_map
        SELECT_OPTIONS[field.name] = tuple(defaults_map.keys())
    PCB_THICKNESS_OPTIONS = presets_store.pcb_thickness_options
    CNC_HOLE_DIMENSION_OPTIONS = presets_store.cnc_hole_dimension_options
    SUBSTRATE_THICKNESS_OPTIONS = presets_store.substrate_thickness_options
    CU_THICKNESS_OPTIONS = presets_store.cu_thickness_options
    PANELIZER_PANEL_OPTIONS = presets_store.panelizer_panel_options
    PANELIZER_JUMBO_MULTIPLIER = presets_store.panelizer_jumbo_multiplier


_refresh_cached_defaults()

PANELIZER_FORM_FIELD_MAP = {
    "CBW": "customer_board_width_max",
    "CBL": "customer_board_length_max",
    "CBWM": "customer_board_width_min",
    "CBLM": "customer_board_length_min",
    "SPW": "single_pcb_width_max",
    "SPL": "single_pcb_length_max",
    "PEW": "panel_edge_margin_w",
    "PEL": "panel_edge_margin_l",
    "BMW": "board_edge_margin_w",
    "BML": "board_edge_margin_l",
    "CW": "inter_board_gap_w",
    "CL": "inter_board_gap_l",
    "SW": "inter_single_gap_w",
    "SL": "inter_single_gap_l",
    "LIMIT": "limit",
    "ARS": "allow_rotate_single_pcb",
    "ARB": "allow_rotate_board",
}
for letter in "ABCDE":
    PANELIZER_FORM_FIELD_MAP[f"SET_{letter}"] = f"include_set_{letter}"


def _panelizer_default_config() -> Dict[str, Any]:
    """Build default panelizer config from DEFAULTS."""
    missing = [key for key in PANELIZER_CONFIG_KEYS if key not in DEFAULTS]
    if missing:
        missing_csv = ", ".join(sorted(missing))
        raise RuntimeError(f"Panelizer defaults missing from presets: {missing_csv}")
    return {key: DEFAULTS[key] for key in PANELIZER_CONFIG_KEYS}


def _panelizer_config(args: Any) -> Dict[str, Any]:
    """Build panelizer config from form args using manipulation module."""
    return build_panelizer_config(args, _panelizer_default_config())


def _panelizer_all_rows(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compute all panelizer rows using manipulation module."""
    return compute_panelizer_rows(cfg, PANELIZER_PANEL_OPTIONS, PANELIZER_JUMBO_MULTIPLIER)


def _panelizer_summary(rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Create panelizer summary using manipulation module."""
    return summarize_panelizer_results(rows, cfg)


def _panelizer_form_defaults(cfg: Dict[str, Any]) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {}
    for form_field, cfg_key in PANELIZER_FORM_FIELD_MAP.items():
        if cfg_key in cfg:
            defaults[form_field] = cfg[cfg_key]
    return defaults


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def _stack_qty_lookup(thickness: str | None, hole_dimension: str | None) -> int | None:
    mapping = DEFAULTS.get("stack_qty_map")
    if not isinstance(mapping, dict):
        return None
    thickness_map = mapping.get(thickness)
    if not isinstance(thickness_map, dict):
        return None
    value = thickness_map.get(hole_dimension)
    if value is None:
        return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return None


def _persist_defaults(
    inputs: Inputs,
    params: Params,
    panelizer_cfg: Optional[Dict[str, Any]] = None,
) -> None:
    presets_store.persist_defaults(
        inputs,
        params,
        panelizer_cfg,
        panelizer_keys=PANELIZER_CONFIG_KEYS,
    )
    _refresh_cached_defaults()
    # NOTE: PresetsStore mutates DEFAULTS in place so later requests see the new defaults.


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------


@app.route("/", methods=["GET", "POST"])
def index():
    context = build_quote_context(
        form=request.form,
        values=request.values,
        method=request.method,
        defaults=DEFAULTS,
        input_type_hints=INPUT_TYPE_HINTS,
        param_type_hints=PARAM_TYPE_HINTS,
        input_field_names=INPUT_FIELD_NAMES,
        priced_fields=PRICED_FIELDS,
        priced_default_maps=PRICED_DEFAULT_MAPS,
        select_options=SELECT_OPTIONS,
        pcb_thickness_options=PCB_THICKNESS_OPTIONS,
        cnc_hole_dimension_options=CNC_HOLE_DIMENSION_OPTIONS,
        substrate_thickness_options=SUBSTRATE_THICKNESS_OPTIONS,
        cu_thickness_options=CU_THICKNESS_OPTIONS,
        stack_qty_lookup=_stack_qty_lookup,
        stack_qty_map=DEFAULTS.get("stack_qty_map", {}),
        priced_client_config=PRICED_CLIENT_CONFIG,
        persist_defaults=_persist_defaults,
        panelizer_config_fn=_panelizer_config,
        panelizer_rows_fn=_panelizer_all_rows,
        panelizer_summary_fn=_panelizer_summary,
        panelizer_form_defaults_fn=_panelizer_form_defaults,
        panelizer_default_config_fn=_panelizer_default_config,
    )
    return render_template("index_q.html", **context)


@app.route("/panelizer-only", methods=["GET", "POST"])
def panelizer_only() -> str:
    context = build_panelizer_only_context(
        values=request.values,
        priced_fields=PRICED_FIELDS,
        select_options=SELECT_OPTIONS,
        priced_default_maps=PRICED_DEFAULT_MAPS,
        stack_qty_map=DEFAULTS.get("stack_qty_map", {}),
        priced_client_config=PRICED_CLIENT_CONFIG,
        panelizer_config_fn=_panelizer_config,
        panelizer_rows_fn=_panelizer_all_rows,
        panelizer_summary_fn=_panelizer_summary,
        panelizer_form_defaults_fn=_panelizer_form_defaults,
        panelizer_default_config_fn=_panelizer_default_config,
    )
    return render_template("panelizer_only.html", **context)


panelizer_app.add_url_rule("/", endpoint="panelizer_only", view_func=panelizer_only, methods=["GET", "POST"])
panelizer_app.add_url_rule(
    "/panelizer-only",
    endpoint="panelizer_only_alias",
    view_func=panelizer_only,
    methods=["GET", "POST"],
)


@app.route("/lt.png")
@app.route("/favicon.ico")
def serve_icon():
    return send_file(ICON_PATH, mimetype="image/png")

panelizer_app.add_url_rule("/lt.png", endpoint="panelizer_icon", view_func=serve_icon)
panelizer_app.add_url_rule("/favicon.ico", endpoint="panelizer_favicon", view_func=serve_icon)


# ---------------------------------------------------------------------------
# Dual server helpers
# ---------------------------------------------------------------------------


@app.before_request
def _bootstrap_panelizer_server() -> None:
    _ensure_panelizer_server()


def _start_panelizer_thread(host: str, port: int, debug: bool) -> None:
    def _run() -> None:
        panelizer_app.run(host=host, port=port, debug=debug, use_reloader=False)

    thread = threading.Thread(target=_run, name="panelizer-only", daemon=True)
    thread.start()
    print(f" * Panelizer-only server running on http://{host}:{port}")


def _panelizer_host(default_host: str | None = None) -> str:
    return os.environ.get("PANELIZER_HOST") or default_host or os.environ.get("HOST", "0.0.0.0")


def _panelizer_port() -> int:
    return int(os.environ.get("PANELIZER_PORT", "5001"))


def _main_port() -> int:
    return int(os.environ.get("PORT", "5000"))


def _should_run_panelizer(debug: bool) -> bool:
    if not debug:
        return True
    return os.environ.get("WERKZEUG_RUN_MAIN") == "true"


def _ensure_panelizer_server(*, host: str | None = None, debug: Optional[bool] = None) -> None:
    global _panelizer_server_started
    if _panelizer_server_started:
        return
    panelizer_port = _panelizer_port()
    if panelizer_port == _main_port():
        return
    if debug is None:
        debug = app.debug
    if not _should_run_panelizer(bool(debug)):
        return
    with _panelizer_server_lock:
        if _panelizer_server_started:
            return
        bind_host = _panelizer_host(host)
        _start_panelizer_thread(bind_host, panelizer_port, bool(debug))
        _panelizer_server_started = True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("DEBUG", "0") in ["1", "true", "True"]
    os.environ.setdefault("PANELIZER_PORT", "5001")
    _ensure_panelizer_server(host=host, debug=debug)
    app.run(host=host, port=port, debug=debug)
