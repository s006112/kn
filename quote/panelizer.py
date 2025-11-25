from __future__ import annotations

# Contains panelizer config keys plus layout enumeration helpers.
# services.build_quote_context/panelizer-only context call these via app_q wrappers.

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

PANELIZER_CONFIG_KEYS: tuple[str, ...] = (
    "customer_board_width_max",
    "customer_board_length_max",
    "customer_board_width_min",
    "customer_board_length_min",
    "single_pcb_width_max",
    "single_pcb_length_max",
    "panel_edge_margin_w",
    "panel_edge_margin_l",
    "board_edge_margin_w",
    "board_edge_margin_l",
    "inter_board_gap_w",
    "inter_board_gap_l",
    "inter_single_gap_w",
    "inter_single_gap_l",
    "allow_rotate_board",
    "allow_rotate_single_pcb",
    "limit",
    "include_set_A",
    "include_set_B",
    "include_set_C",
    "include_set_D",
    "include_set_E",
)


def build_panelizer_config(args: Any, defaults: Mapping[str, Any]) -> Dict[str, Any]:
    missing = [key for key in PANELIZER_CONFIG_KEYS if key not in defaults]
    if missing:
        missing_csv = ", ".join(sorted(missing))
        raise RuntimeError(f"Panelizer defaults missing from presets: {missing_csv}")

    cfg = {key: defaults[key] for key in PANELIZER_CONFIG_KEYS}

    cfg["customer_board_width_max"] = _panelizer_float(args, "CBW", cfg.get("customer_board_width_max", 0.0))
    cfg["customer_board_length_max"] = _panelizer_float(args, "CBL", cfg.get("customer_board_length_max", 0.0))
    cfg["customer_board_width_min"] = _panelizer_float(args, "CBWM", cfg.get("customer_board_width_min", 0.0))
    cfg["customer_board_length_min"] = _panelizer_float(args, "CBLM", cfg.get("customer_board_length_min", 0.0))
    cfg["single_pcb_width_max"] = _panelizer_float(args, "SPW", cfg.get("single_pcb_width_max", 0.0))
    cfg["single_pcb_length_max"] = _panelizer_float(args, "SPL", cfg.get("single_pcb_length_max", 0.0))
    cfg["panel_edge_margin_w"] = _panelizer_float(args, "PEW", cfg.get("panel_edge_margin_w", 0.0))
    cfg["panel_edge_margin_l"] = _panelizer_float(args, "PEL", cfg.get("panel_edge_margin_l", 0.0))
    cfg["board_edge_margin_w"] = _panelizer_float(args, "BMW", cfg.get("board_edge_margin_w", 0.0))
    cfg["board_edge_margin_l"] = _panelizer_float(args, "BML", cfg.get("board_edge_margin_l", 0.0))
    cfg["inter_board_gap_w"] = _panelizer_float(args, "CW", cfg.get("inter_board_gap_w", 0.0))
    cfg["inter_board_gap_l"] = _panelizer_float(args, "CL", cfg.get("inter_board_gap_l", 0.0))
    cfg["inter_single_gap_w"] = _panelizer_float(args, "SW", cfg.get("inter_single_gap_w", 0.0))
    cfg["inter_single_gap_l"] = _panelizer_float(args, "SL", cfg.get("inter_single_gap_l", 0.0))
    cfg["allow_rotate_board"] = _panelizer_checkbox(args, "ARB", cfg.get("allow_rotate_board", False))
    cfg["allow_rotate_single_pcb"] = _panelizer_checkbox(args, "ARS", cfg.get("allow_rotate_single_pcb", False))
    cfg["limit"] = _panelizer_int(args, "LIMIT", int(cfg.get("limit", 10)))
    for letter in "ABCDE":
        cfg[f"include_set_{letter}"] = _panelizer_checkbox(args, f"SET_{letter}", cfg.get(f"include_set_{letter}", False))
    return cfg


def compute_panelizer_rows(
    cfg: Mapping[str, Any],
    panel_options: Mapping[str, Tuple[float, float]],
    jumbo_multiplier: Mapping[str, int],
) -> List[Dict[str, Any]]:
    enabled_sets = {letter for letter in "ABCDE" if cfg.get(f"include_set_{letter}", False)}
    if not enabled_sets:
        return []

    rows: List[Dict[str, Any]] = []
    for style, (pw, pl) in panel_options.items():
        if style[:1].upper() not in enabled_sets:
            continue
        rows.extend(_panelizer_enumerate_layouts(cfg, pw, pl, style, jumbo_multiplier))

    rows.sort(
        key=lambda r: (
            -r["pcbs_per_jumbo"],
            -r["utilization"],
            r["objective_key"],
        )
    )
    return _panelizer_deduplicate_rows(rows)


def summarize_panelizer_results(rows: List[Dict[str, Any]], cfg: Mapping[str, Any]) -> Dict[str, Any]:
    limit = int(cfg.get("limit", 10))
    total = len(rows)
    shown = min(total, limit)
    display_rows = rows[:shown]

    if rows:
        message = f"Found {total} feasible layouts. Showing top {shown} by PCBs per Jumbo."
    else:
        message = "No feasible layouts under current constraints."

    max_pcbs = max((r["pcbs_per_jumbo"] for r in rows), default=None)
    star_message = "Highest PCBs per Jumbo shown with ★" if max_pcbs is not None else ""
    table_attrs = "" if display_rows else 'style="display:none"'

    return {
        "rows": display_rows,
        "message": message,
        "limit": limit,
        "total": total,
        "shown": shown,
        "max_pcbs_per_jumbo": max_pcbs,
        "star_message": star_message,
        "table_attrs": table_attrs,
    }


def _panelizer_parse_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes")
    return bool(value)


def _panelizer_checkbox(args: Any, key: str, default: bool) -> bool:
    if key in args:
        raw = args.get(key, "on")
        normalized = "on" if raw in (None, "") else raw
        return _panelizer_parse_bool(normalized)
    if not args:
        return default
    return False


def _panelizer_float(args: Any, key: str, default: float) -> float:
    raw = args.get(key)
    if raw in (None, ""):
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _panelizer_int(args: Any, key: str, default: int) -> int:
    raw = args.get(key)
    if raw in (None, ""):
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _panelizer_almost_le(a: float, b: float, eps: float = 1e-9) -> bool:
    return a <= b + eps


def _panelizer_almost_ge(a: float, b: float, eps: float = 1e-9) -> bool:
    return a + eps >= b


def _panelizer_rects_overlap_1d(a0: float, a1: float, b0: float, b1: float, eps: float = 1e-9) -> bool:
    return (a0 < b1 - eps) and (b0 < a1 - eps)


def _panelizer_pairwise_no_overlap(rects: List[Tuple[float, float, float, float]], eps: float = 1e-9) -> bool:
    n = len(rects)
    for i in range(n):
        xi0, yi0, xi1, yi1 = rects[i]
        for j in range(i + 1, n):
            xj0, yj0, xj1, yj1 = rects[j]
            if _panelizer_rects_overlap_1d(xi0, xi1, xj0, xj1, eps) and _panelizer_rects_overlap_1d(yi0, yi1, yj0, yj1, eps):
                return False
    return True


def _panelizer_upper_bound_grid(max_len: float, item: float, gap: float) -> int:
    if item <= 0:
        return 0
    return max(0, int(math.floor((max_len + gap) / (item + gap))))


def _panelizer_utilization(total_single_pcbs: int, spw: float, spl: float, wpw: float, wpl: float) -> float:
    return (total_single_pcbs * spw * spl) / (wpw * wpl) if wpw > 0 and wpl > 0 else 0.0


def _panelizer_enumerate_layouts(
    cfg: Mapping[str, float],
    panel_w: float,
    panel_l: float,
    panel_style: str,
    jumbo_multiplier: Mapping[str, int],
) -> List[Dict[str, Any]]:
    WPW = float(panel_w)
    WPL = float(panel_l)
    CBW = float(cfg["customer_board_width_max"])
    CBL = float(cfg["customer_board_length_max"])
    CBW_min = float(cfg.get("customer_board_width_min", 0.0))
    CBL_min = float(cfg.get("customer_board_length_min", 0.0))
    SPW = float(cfg["single_pcb_width_max"])
    SPL = float(cfg["single_pcb_length_max"])
    if (SPW + SPL) <= 49.9:
        return []
    PEW = float(cfg["panel_edge_margin_w"])
    PEL = float(cfg["panel_edge_margin_l"])
    BEW = float(cfg.get("board_edge_margin_w", 0.0))
    BEL = float(cfg.get("board_edge_margin_l", 0.0))
    CW = float(cfg["inter_board_gap_w"])
    CL = float(cfg["inter_board_gap_l"])
    SW = float(cfg["inter_single_gap_w"])
    SL = float(cfg["inter_single_gap_l"])
    allow_rotate_board = bool(cfg.get("allow_rotate_board", False))
    allow_rotate_single = bool(cfg.get("allow_rotate_single_pcb", False))
    CWi, CLi = CW, CL
    SWi, SLi = SW, SL

    panel_area = WPW * WPL

    layouts: List[Dict[str, Any]] = []
    board_rot_options = [False, True] if allow_rotate_board else [False]
    single_rot_options = [False, True] if allow_rotate_single else [False]

    jmul = jumbo_multiplier.get(panel_style, 1)
    best_pcbs_per_jumbo = 0

    for board_rot in board_rot_options:
        if board_rot:
            CBW_eff, CBL_eff = CBL, CBW
            CBW_min_eff, CBL_min_eff = CBL_min, CBW_min
            margin_w_eff, margin_l_eff = BEL, BEW
        else:
            CBW_eff, CBL_eff = CBW, CBL
            CBW_min_eff, CBL_min_eff = CBW_min, CBL_min
            margin_w_eff, margin_l_eff = BEW, BEL

        max_inner_w = CBW_eff - 2.0 * margin_w_eff
        max_inner_l = CBL_eff - 2.0 * margin_l_eff
        if max_inner_w <= 0 or max_inner_l <= 0:
            continue

        for single_rot in single_rot_options:
            spw_eff, spl_eff = (SPL, SPW) if single_rot else (SPW, SPL)

            ub_nw = _panelizer_upper_bound_grid(max_inner_w, spw_eff, SWi)
            ub_nl = _panelizer_upper_bound_grid(max_inner_l, spl_eff, SLi)
            if ub_nw == 0 or ub_nl == 0:
                continue

            for nw in range(1, ub_nw + 1):
                single_grid_w = nw * spw_eff + (nw - 1) * SWi
                if not _panelizer_almost_le(single_grid_w, max_inner_w):
                    continue
                for nl in range(1, ub_nl + 1):
                    single_grid_l = nl * spl_eff + (nl - 1) * SLi
                    if not _panelizer_almost_le(single_grid_l, max_inner_l):
                        continue

                    board_w = single_grid_w + 2.0 * margin_w_eff
                    board_l = single_grid_l + 2.0 * margin_l_eff
                    if not _panelizer_almost_ge(board_w, CBW_min_eff) or not _panelizer_almost_ge(board_l, CBL_min_eff):
                        continue
                    avail_w = WPW - 2.0 * PEW
                    avail_l = WPL - 2.0 * PEL
                    if avail_w <= 0 or avail_l <= 0:
                        continue

                    ub_nbw = _panelizer_upper_bound_grid(avail_w, board_w, CWi)
                    ub_nbl = _panelizer_upper_bound_grid(avail_l, board_l, CLi)
                    if ub_nbw == 0 or ub_nbl == 0:
                        continue

                    max_pcbs_this = ub_nbw * ub_nbl * nw * nl * jmul
                    if max_pcbs_this < best_pcbs_per_jumbo:
                        continue

                    for nbw in range(1, ub_nbw + 1):
                        panel_used_w = nbw * board_w + (nbw - 1) * CWi + 2.0 * PEW
                        if not _panelizer_almost_le(panel_used_w, WPW):
                            continue
                        for nbl in range(1, ub_nbl + 1):
                            panel_used_l = nbl * board_l + (nbl - 1) * CLi + 2.0 * PEL
                            if not _panelizer_almost_le(panel_used_l, WPL):
                                continue

                            total_single_pcbs = nbw * nbl * nw * nl
                            util = _panelizer_utilization(total_single_pcbs, SPW, SPL, WPW, WPL)
                            pcbs_per_jumbo = total_single_pcbs * jmul
                            unused_area = panel_area - panel_used_w * panel_used_l
                            rotations_count = (1 if board_rot else 0) + (1 if single_rot else 0)
                            left_margin = PEW
                            bottom_margin = PEL
                            right_margin = WPW - panel_used_w
                            top_margin = WPL - panel_used_l
                            mu_score = abs(left_margin - right_margin) + abs(bottom_margin - top_margin)

                            board_origins = []
                            x0, y0 = PEW, PEL
                            for j in range(nbl):
                                y = y0 + j * (board_l + CLi)
                                for i in range(nbw):
                                    x = x0 + i * (board_w + CWi)
                                    board_origins.append({"x": x, "y": y, "rotated": board_rot})

                            single_origins = []
                            sx0, sy0 = margin_w_eff, margin_l_eff
                            for jl in range(nl):
                                sy = sy0 + jl * (spl_eff + SLi)
                                for iw in range(nw):
                                    sx = sx0 + iw * (spw_eff + SWi)
                                    single_origins.append({"x": sx, "y": sy, "rotated": single_rot})

                            all_ok = True
                            failure: Optional[str] = None

                            if all_ok:
                                spw_e, spl_e = spw_eff, spl_eff
                                single_rects = []
                                for so in single_origins:
                                    sx, sy = so["x"], so["y"]
                                    single_rects.append((sx, sy, sx + spw_e, sy + spl_e))
                                    if sx < 0 or sy < 0 or (sx + spw_e) > board_w or (sy + spl_e) > board_l:
                                        all_ok = False
                                        failure = "Single out of board bounds"
                                        break
                                if all_ok and not _panelizer_pairwise_no_overlap(single_rects):
                                    all_ok = False
                                    failure = "Singles overlap"

                            if all_ok and pcbs_per_jumbo > best_pcbs_per_jumbo:
                                best_pcbs_per_jumbo = pcbs_per_jumbo

                            layouts.append(
                                {
                                    "total_single_pcbs": total_single_pcbs,
                                    "utilization": util,
                                    "unused_area": unused_area,
                                    "nbw": nbw,
                                    "nbl": nbl,
                                    "nw": nw,
                                    "nl": nl,
                                    "board_rot": board_rot,
                                    "single_rot": single_rot,
                                    "board_w": board_w,
                                    "board_l": board_l,
                                    "panel_used_w": panel_used_w,
                                    "panel_used_l": panel_used_l,
                                    "panel_style": panel_style,
                                    "panel_width": WPW,
                                    "panel_length": WPL,
                                    "pcbs_per_jumbo": pcbs_per_jumbo,
                                    "margins": {
                                        "left": left_margin,
                                        "right": right_margin,
                                        "bottom": bottom_margin,
                                        "top": top_margin,
                                    },
                                    "margin_uniformity": mu_score,
                                    "rotations_count": rotations_count,
                                    "placements": {
                                        "boards": board_origins,
                                        "singles_per_board": single_origins,
                                    },
                                    "all_constraints_satisfied": all_ok,
                                    "first_failure": failure,
                                    "objective_key": (
                                        -total_single_pcbs,
                                        -util,
                                        unused_area,
                                        mu_score,
                                        rotations_count,
                                    ),
                                }
                            )
    return layouts


def _panelizer_rotation_priority(row: Dict[str, Any]) -> int:
    board_rot = row.get("board_rot", False)
    single_rot = row.get("single_rot", False)
    if not board_rot and not single_rot:
        return 0
    if board_rot and not single_rot:
        return 1
    if not board_rot and single_rot:
        return 2
    return 3


def _panelizer_deduplicate_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen_order: List[Tuple[str, int, int, int, int, int]] = []
    best: Dict[Tuple[str, int, int, int, int, int], Dict[str, Any]] = {}
    for row in rows:
        key = (
            row.get("panel_style"),
            row["total_single_pcbs"],
            row["nbw"],
            row["nbl"],
            row["nw"],
            row["nl"],
        )
        if key not in best:
            best[key] = row
            seen_order.append(key)
            continue
        current = best[key]
        if _panelizer_rotation_priority(row) < _panelizer_rotation_priority(current):
            best[key] = row
    return [best[key] for key in seen_order]
