from __future__ import annotations

# Provides PresetsStore for loading/merging defaults and exposes derived options.
# Called by app_q.py and services.py (via PresetsStore) to read/update DEFAULTS safely.

import json
import os
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Dict, Iterable, Mapping, Tuple


def _load_json(path: str, *, required: bool = False) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        if required:
            raise
        return {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = base.copy()
    for key, value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(base_value, value)
        else:
            merged[key] = value
    return merged


class PresetsStore:
    """Load and manage presets plus derived default-driven options."""

    def __init__(self, *, base_path: str | None = None, override_path: str | None = None) -> None:
        base_dir = os.path.dirname(__file__)
        default_base = os.path.join(base_dir, "presets_q.json")
        default_override = os.path.join(base_dir, "presets_q.local.json")
        self._base_path = base_path or default_base
        env_override = os.environ.get("PRESETS_OVERRIDE_PATH")
        self._override_path = override_path or env_override or default_override
        self._load()

    def _load(self) -> None:
        self._presets_base = _load_json(self._base_path, required=True)
        self._presets_override = _load_json(self._override_path)
        self._presets = _deep_merge(self._presets_base, self._presets_override)
        defaults = self._presets.get("defaults")
        if not isinstance(defaults, dict):
            raise RuntimeError("defaults missing from presets.")
        self._defaults = defaults
        self._refresh_derived()

    def _refresh_derived(self) -> None:
        self._pcb_thickness_options = self._options_from_defaults("pcb_thickness_options")
        self._cnc_hole_dimension_options = self._options_from_defaults("cnc_hole_dimension_options")
        self._substrate_thickness_options = self._options_from_defaults("substrate_thickness_options")
        self._cu_thickness_options = self._options_from_defaults("cu_thickness_options")
        self._panelizer_panel_options = self._load_panelizer_panel_options()
        self._panelizer_jumbo_multiplier = self._load_panelizer_jumbo_multiplier()

    @property
    def presets(self) -> dict[str, Any]:
        return self._presets

    @property
    def defaults(self) -> dict[str, Any]:
        return self._defaults

    @property
    def pcb_thickness_options(self) -> tuple[str, ...]:
        return self._pcb_thickness_options

    @property
    def cnc_hole_dimension_options(self) -> tuple[str, ...]:
        return self._cnc_hole_dimension_options

    @property
    def substrate_thickness_options(self) -> tuple[str, ...]:
        return self._substrate_thickness_options

    @property
    def cu_thickness_options(self) -> tuple[str, ...]:
        return self._cu_thickness_options

    @property
    def panelizer_panel_options(self) -> Dict[str, Tuple[float, float]]:
        return self._panelizer_panel_options

    @property
    def panelizer_jumbo_multiplier(self) -> Dict[str, int]:
        return self._panelizer_jumbo_multiplier

    def defaults_map(self, key: str) -> dict[str, Any]:
        value = self._defaults.get(key, {})
        return deepcopy(value) if isinstance(value, dict) else {}

    def options_from_defaults(self, key: str) -> tuple[str, ...]:
        mapping = self._defaults.get(key, {})
        return tuple(mapping.keys()) if isinstance(mapping, dict) else tuple()

    def persist_defaults(
        self,
        inputs: Any,
        params: Any,
        panelizer_cfg: Mapping[str, Any] | None = None,
        *,
        panelizer_keys: Iterable[str] = (),
    ) -> None:
        updated_defaults = self._defaults.copy()
        updated_defaults.update(asdict(inputs))
        updated_defaults.update(asdict(params))
        if panelizer_cfg:
            for key in panelizer_keys:
                if key in panelizer_cfg:
                    updated_defaults[key] = panelizer_cfg[key]

        try:
            self._presets_override["defaults"] = updated_defaults
            with open(self._override_path, "w", encoding="utf-8") as handle:
                json.dump(self._presets_override, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
        except OSError as exc:
            raise RuntimeError(f"Failed to update presets: {exc}") from exc

        self._defaults.clear()
        self._defaults.update(updated_defaults)
        self._presets["defaults"] = self._defaults
        self._refresh_derived()

    def _options_from_defaults(self, key: str) -> tuple[str, ...]:
        mapping = self._defaults.get(key, {})
        return tuple(mapping.keys()) if isinstance(mapping, dict) else tuple()

    def _load_panelizer_panel_options(self) -> Dict[str, Tuple[float, float]]:
        section = self._defaults.get("panelizer_panel_options", {})
        if not section:
            raise RuntimeError("panelizer_panel_options is missing from defaults.")
        options: Dict[str, Tuple[float, float]] = {}
        for style, dims in section.items():
            if not isinstance(dims, (list, tuple)) or len(dims) != 2:
                raise ValueError(f"Invalid panel dimensions for {style!r}")
            options[style] = (float(dims[0]), float(dims[1]))
        return options

    def _load_panelizer_jumbo_multiplier(self) -> Dict[str, int]:
        section = self._defaults.get("panelizer_jumbo_multiplier", {})
        if not section:
            raise RuntimeError("panelizer_jumbo_multiplier is missing from defaults.")
        multipliers: Dict[str, int] = {}
        for style, value in section.items():
            multipliers[style] = int(value)
        return multipliers
