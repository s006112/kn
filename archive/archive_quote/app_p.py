import os
from wsgiref.simple_server import make_server
from urllib.parse import parse_qs
from html import escape
import json
import math
from typing import Any, Dict, Tuple, List, Optional

BASE_DIR = os.path.dirname(__file__)
PRESETS_FILE = os.path.join(BASE_DIR, "presets.json")
PRESETS_LOCAL_FILE = os.path.join(BASE_DIR, "presets.local.json")


def _load_json_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


_BASE_PRESETS = _load_json_file(PRESETS_FILE)
_LOCAL_PRESETS = _load_json_file(PRESETS_LOCAL_FILE)


def _merged_section(key: str) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    base_val = _BASE_PRESETS.get(key)
    if isinstance(base_val, dict):
        merged.update(base_val)
    local_val = _LOCAL_PRESETS.get(key)
    if isinstance(local_val, dict):
        merged.update(local_val)
    return merged


def _load_panel_options() -> Dict[str, Tuple[float, float]]:
    raw = _merged_section("panelizer_panel_options")
    if not raw:
        raise RuntimeError("panelizer_panel_options is missing from presets.")
    options: Dict[str, Tuple[float, float]] = {}
    for style, dims in raw.items():
        if not isinstance(dims, (list, tuple)) or len(dims) != 2:
            raise ValueError(f"Invalid panel dimensions for {style!r}")
        options[style] = (float(dims[0]), float(dims[1]))
    return options


def _load_jumbo_multiplier() -> Dict[str, int]:
    raw = _merged_section("panelizer_jumbo_multiplier")
    if not raw:
        raise RuntimeError("panelizer_jumbo_multiplier is missing from presets.")
    multipliers: Dict[str, int] = {}
    for style, value in raw.items():
        multipliers[style] = int(value)
    return multipliers


def _load_panelizer_defaults() -> Dict[str, Any]:
    defaults = _merged_section("panelizer_defaults")
    if not defaults:
        raise RuntimeError("panelizer_defaults is missing from presets.")
    return defaults


PANEL_OPTIONS: Dict[str, Tuple[float, float]] = _load_panel_options()
JUMBO_MULTIPLIER: Dict[str, int] = _load_jumbo_multiplier()


def default_config() -> Dict[str, float]:
    defaults = _load_panelizer_defaults()
    return dict(defaults)

def _almost_le(a: float, b: float, eps: float = 1e-9) -> bool:
    return a <= b + eps

def _almost_ge(a: float, b: float, eps: float = 1e-9) -> bool:
    return a + eps >= b

def _rects_overlap_1d(a0: float, a1: float, b0: float, b1: float, eps: float = 1e-9) -> bool:
    return (a0 < b1 - eps) and (b0 < a1 - eps)

def _pairwise_no_overlap(rects: List[Tuple[float, float, float, float]], eps: float = 1e-9) -> bool:
    n = len(rects)
    for i in range(n):
        xi0, yi0, xi1, yi1 = rects[i]
        for j in range(i + 1, n):
            xj0, yj0, xj1, yj1 = rects[j]
            if _rects_overlap_1d(xi0, xi1, xj0, xj1, eps) and _rects_overlap_1d(yi0, yi1, yj0, yj1, eps):
                return False
    return True

def _upper_bound_grid(max_len: float, item: float, gap: float) -> int:
    if item <= 0:
        return 0
    return max(0, int(math.floor((max_len + gap) / (item + gap))))

def _utilization(total_single_pcbs: int, spw: float, spl: float, wpw: float, wpl: float) -> float:
    return (total_single_pcbs * spw * spl) / (wpw * wpl) if wpw > 0 and wpl > 0 else 0.0

def enumerate_layouts(cfg: Dict[str, float], panel_w: float, panel_l: float, panel_style: str) -> List[Dict]:
    # Extract
    WPW = float(panel_w)
    WPL = float(panel_l)
    CBW = float(cfg["customer_board_width_max"])
    CBL = float(cfg["customer_board_length_max"])
    CBW_min = float(cfg.get("customer_board_width_min", 0.0))
    CBL_min = float(cfg.get("customer_board_length_min", 0.0))
    SPW = float(cfg["single_pcb_width_max"])
    SPL = float(cfg["single_pcb_length_max"])
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
    kerf = float(cfg.get("kerf_allowance", 0.0))

    # Inflate gaps
    CWi, CLi = CW + kerf, CL + kerf
    SWi, SLi = SW + kerf, SL + kerf

    panel_area = WPW * WPL

    layouts: List[Dict] = []
    board_rot_options = [False, True] if allow_rotate_board else [False]
    single_rot_options = [False, True] if allow_rotate_single else [False]

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

            ub_nw = _upper_bound_grid(max_inner_w, spw_eff, SWi)
            ub_nl = _upper_bound_grid(max_inner_l, spl_eff, SLi)
            if ub_nw == 0 or ub_nl == 0:
                continue

            for nw in range(1, ub_nw + 1):
                single_grid_w = nw * spw_eff + (nw - 1) * SWi
                if not _almost_le(single_grid_w, max_inner_w):
                    continue
                for nl in range(1, ub_nl + 1):
                    single_grid_l = nl * spl_eff + (nl - 1) * SLi
                    if not _almost_le(single_grid_l, max_inner_l):
                        continue

                    board_w = single_grid_w + 2.0 * margin_w_eff
                    board_l = single_grid_l + 2.0 * margin_l_eff
                    if not _almost_ge(board_w, CBW_min_eff) or not _almost_ge(board_l, CBL_min_eff):
                        continue
                    avail_w = WPW - 2.0 * PEW
                    avail_l = WPL - 2.0 * PEL
                    if avail_w <= 0 or avail_l <= 0:
                        continue

                    ub_nbw = _upper_bound_grid(avail_w, board_w, CWi)
                    ub_nbl = _upper_bound_grid(avail_l, board_l, CLi)
                    if ub_nbw == 0 or ub_nbl == 0:
                        continue

                    for nbw in range(1, ub_nbw + 1):
                        panel_used_w = nbw * board_w + (nbw - 1) * CWi + 2.0 * PEW
                        if not _almost_le(panel_used_w, WPW):
                            continue
                        for nbl in range(1, ub_nbl + 1):
                            panel_used_l = nbl * board_l + (nbl - 1) * CLi + 2.0 * PEL
                            if not _almost_le(panel_used_l, WPL):
                                continue

                            total_single_pcbs = nbw * nbl * nw * nl
                            util = _utilization(total_single_pcbs, SPW, SPL, WPW, WPL)
                            jumbo_multiplier = JUMBO_MULTIPLIER.get(panel_style, 1)
                            pcbs_per_jumbo = total_single_pcbs * jumbo_multiplier
                            unused_area = panel_area - panel_used_w * panel_used_l
                            rotations_count = (1 if board_rot else 0) + (1 if single_rot else 0)
                            left_margin = PEW
                            bottom_margin = PEL
                            right_margin = WPW - panel_used_w
                            top_margin = WPL - panel_used_l
                            mu_score = abs(left_margin - right_margin) + abs(bottom_margin - top_margin)

                            # Placements (for first N rows we display summary; full JSON available)
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

                            # Validation
                            all_ok = True
                            failure: Optional[str] = None
                            # Board limit under rotation
                            if not _almost_le(board_w, (CBL if board_rot else CBW)):  # width
                                all_ok, failure = False, "Board width exceeds limit"
                            if all_ok and not _almost_le(board_l, (CBW if board_rot else CBL)):  # length
                                all_ok, failure = False, "Board length exceeds limit"
                            if all_ok and not _almost_ge(board_w, CBW_min_eff):
                                all_ok, failure = False, "Board width below minimum"
                            if all_ok and not _almost_ge(board_l, CBL_min_eff):
                                all_ok, failure = False, "Board length below minimum"
                            # Board rectangles
                            board_rects = []
                            for bo in board_origins:
                                x, y = bo["x"], bo["y"]
                                board_rects.append((x, y, x + board_w, y + board_l))
                                if x < 0 or y < 0 or (x + board_w) > WPW or (y + board_l) > WPL:
                                    all_ok, failure = False, "Board out of panel bounds"
                                    break
                            if all_ok and not _pairwise_no_overlap(board_rects):
                                all_ok, failure = False, "Boards overlap"
                            # Singles inside local board
                            if all_ok:
                                spw_e, spl_e = ((SPL, SPW) if single_rot else (SPW, SPL))
                                single_rects = []
                                for so in single_origins:
                                    sx, sy = so["x"], so["y"]
                                    single_rects.append((sx, sy, sx + spw_e, sy + spl_e))
                                    if sx < 0 or sy < 0 or (sx + spw_e) > board_w or (sy + spl_e) > board_l:
                                        all_ok, failure = False, "Single out of board bounds"
                                        break
                                if all_ok and not _pairwise_no_overlap(single_rects):
                                    all_ok, failure = False, "Singles overlap"

                            layouts.append({
                                "total_single_pcbs": total_single_pcbs,
                                "utilization": util,
                                "unused_area": unused_area,
                                "nbw": nbw, "nbl": nbl,
                                "nw": nw, "nl": nl,
                                "board_rot": board_rot, "single_rot": single_rot,
                                "board_w": board_w, "board_l": board_l,
                                "panel_used_w": panel_used_w, "panel_used_l": panel_used_l,
                                "panel_style": panel_style,
                                "panel_width": WPW,
                                "panel_length": WPL,
                                "pcbs_per_jumbo": pcbs_per_jumbo,
                                "margins": {"left": left_margin, "right": right_margin,
                                            "bottom": bottom_margin, "top": top_margin},
                                "margin_uniformity": mu_score,
                                "rotations_count": rotations_count,
                                "placements": {
                                    "boards": board_origins,
                                    "singles_per_board": single_origins
                                },
                                "all_constraints_satisfied": all_ok,
                                "first_failure": failure,
                                # Primary objective sort key (for reference)
                                "objective_key": (
                                    -total_single_pcbs,          # maximize
                                    -util,                       # maximize
                                    unused_area,                 # minimize
                                    mu_score,                    # minimize
                                    rotations_count,             # minimize
                                ),
                            })

    return layouts

# ------------------------------ Web Utilities --------------------------------

ICON_FILENAME = "lt.png"
ICON_PATH = os.path.join(os.path.dirname(__file__), ICON_FILENAME)
CSS_FILENAME = "panel.css"
CSS_PATH = os.path.join(os.path.dirname(__file__), "static", CSS_FILENAME)

def _serve_static_asset(path: str, content_type: str, method: str, start_response):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8"),
                                         ("Content-Length", "0")])
        return [b""]
    headers = [("Content-Type", content_type), ("Content-Length", str(len(data)))]
    start_response("200 OK", headers)
    if method == "HEAD":
        return [b""]
    return [data]

def parse_bool(v: str) -> bool:
    if isinstance(v, str):
        return v.lower() in ("1", "true", "on", "yes")
    return bool(v)


def parse_checkbox(qs: dict, key: str, default: bool) -> bool:
    if key in qs:
        values = qs.get(key, [])
        raw = values[0] if values else "on"
        raw = raw if raw is not None else "on"
        return parse_bool(raw if raw != "" else "on")
    if not qs:
        return default
    return False

def parse_float(qs: dict, key: str, default: float) -> float:
    try:
        return float(qs.get(key, [default])[0])
    except Exception:
        return default

def parse_int(qs: dict, key: str, default: int) -> int:
    try:
        return int(qs.get(key, [default])[0])
    except Exception:
        return default

def parse_cfg(qs: dict) -> Dict[str, float]:
    d = default_config()
    d["customer_board_width_max"]  = parse_float(qs, "CBW", d["customer_board_width_max"])
    d["customer_board_length_max"] = parse_float(qs, "CBL", d["customer_board_length_max"])
    d["customer_board_width_min"]  = parse_float(qs, "CBWM", d["customer_board_width_min"])
    d["customer_board_length_min"] = parse_float(qs, "CBLM", d["customer_board_length_min"])
    d["single_pcb_width_max"]  = parse_float(qs, "SPW", d["single_pcb_width_max"])
    d["single_pcb_length_max"] = parse_float(qs, "SPL", d["single_pcb_length_max"])
    d["panel_edge_margin_w"] = parse_float(qs, "PEW", d["panel_edge_margin_w"])
    d["panel_edge_margin_l"] = parse_float(qs, "PEL", d["panel_edge_margin_l"])
    d["board_edge_margin_w"] = parse_float(qs, "BMW", d["board_edge_margin_w"])
    d["board_edge_margin_l"] = parse_float(qs, "BML", d["board_edge_margin_l"])
    d["inter_board_gap_w"] = parse_float(qs, "CW", d["inter_board_gap_w"])
    d["inter_board_gap_l"] = parse_float(qs, "CL", d["inter_board_gap_l"])
    d["inter_single_gap_w"] = parse_float(qs, "SW", d["inter_single_gap_w"])
    d["inter_single_gap_l"] = parse_float(qs, "SL", d["inter_single_gap_l"])
    d["allow_rotate_board"] = parse_checkbox(qs, "ARB", d["allow_rotate_board"])
    d["allow_rotate_single_pcb"] = parse_checkbox(qs, "ARS", d["allow_rotate_single_pcb"])
    d["kerf_allowance"] = parse_float(qs, "KERF", d["kerf_allowance"])
    d["limit"] = parse_int(qs, "LIMIT", d["limit"])
    d["include_set_A"] = parse_checkbox(qs, "SET_A", d["include_set_A"])
    d["include_set_B"] = parse_checkbox(qs, "SET_B", d["include_set_B"])
    d["include_set_C"] = parse_checkbox(qs, "SET_C", d["include_set_C"])
    d["include_set_D"] = parse_checkbox(qs, "SET_D", d["include_set_D"])
    d["include_set_E"] = parse_checkbox(qs, "SET_E", d["include_set_E"])
    return d

# Legacy HTML helpers removed after template extraction

def _rotation_priority(row: Dict) -> int:
    board_rot = row.get("board_rot", False)
    single_rot = row.get("single_rot", False)
    if not board_rot and not single_rot:
        return 0
    if board_rot and not single_rot:
        return 1
    if not board_rot and single_rot:
        return 2
    return 3

def deduplicate_rows(rows: List[Dict]) -> List[Dict]:
    seen_order: List[Tuple[str, int, int, int, int, int]] = []
    best: Dict[Tuple[str, int, int, int, int, int], Dict] = {}
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
        if _rotation_priority(row) < _rotation_priority(current):
            best[key] = row

    return [best[k] for k in seen_order]

def page(cfg: Dict[str, float], rows: List[Dict]) -> str:
    total = len(rows)
    limit = int(cfg.get("limit", 10))
    max_pcbs_jumbo = max((r["pcbs_per_jumbo"] for r in rows), default=None)

    row_html: List[str] = []
    if rows:
        shown = min(total, limit)
        results_message = (
            f"Found {total} feasible layouts. Showing top {shown} by PCBs per Jumbo."
        )
        for idx, r in enumerate(rows[:shown]):
            star = " ★" if max_pcbs_jumbo is not None and r["pcbs_per_jumbo"] == max_pcbs_jumbo else ""
            util = f"{r['utilization'] * 100:.2f}%"
            board_sz = f"{r['board_w']:.1f}×{r['board_l']:.1f}"
            panel_style = r["panel_style"]
            panel_size = f"{r['panel_width']:.1f}×{r['panel_length']:.1f}"
            rot = ("B" if r["board_rot"] else "") + ("S" if r["single_rot"] else "")
            rot = rot if rot else "—"
            row_html.append(
                "<tr>"
                f"<td class='l'>{idx+1}{star}</td>"
                f"<td>{r['pcbs_per_jumbo']}</td>"
                f"<td>{r['total_single_pcbs']}</td>"
                f"<td>{r['nbw']}×{r['nbl']}</td>"
                f"<td>{r['nw']}×{r['nl']}</td>"
                f"<td>{board_sz}</td>"
                f"<td>{panel_style}</td>"
                f"<td>{panel_size}</td>"
                f"<td>{util}</td>"
                f"<td>{rot}</td>"
                "</tr>"
            )
        table_attrs = ""
    else:
        results_message = "No feasible layouts under current constraints."
        table_attrs = 'style="display:none"'

    results_rows = "".join(row_html)

    # Simple template substitution
    tmpl_path = os.path.join(os.path.dirname(__file__), "templates", "index_p.html")
    with open(tmpl_path, "r", encoding="utf-8") as fh:
        html = fh.read()

    def chk(val: bool) -> str:
        return "checked" if bool(val) else ""

    repl = {
        "{{VAL_SPW}}": str(cfg["single_pcb_width_max"]),
        "{{VAL_SPL}}": str(cfg["single_pcb_length_max"]),
        "{{CHK_ARS}}": chk(cfg.get("allow_rotate_single_pcb", False)),
        "{{VAL_PEW}}": str(cfg["panel_edge_margin_w"]),
        "{{VAL_PEL}}": str(cfg["panel_edge_margin_l"]),
        "{{CHK_SET_A}}": chk(cfg.get("include_set_A", False)),
        "{{CHK_SET_B}}": chk(cfg.get("include_set_B", False)),
        "{{CHK_SET_C}}": chk(cfg.get("include_set_C", False)),
        "{{CHK_SET_D}}": chk(cfg.get("include_set_D", False)),
        "{{CHK_SET_E}}": chk(cfg.get("include_set_E", False)),
        "{{VAL_BMW}}": str(cfg["board_edge_margin_w"]),
        "{{VAL_BML}}": str(cfg["board_edge_margin_l"]),
        "{{VAL_CBW}}": str(cfg["customer_board_width_max"]),
        "{{VAL_CBL}}": str(cfg["customer_board_length_max"]),
        "{{VAL_CBWM}}": str(cfg["customer_board_width_min"]),
        "{{VAL_CBLM}}": str(cfg["customer_board_length_min"]),
        "{{CHK_ARB}}": chk(cfg.get("allow_rotate_board", False)),
        "{{VAL_CW}}": str(cfg["inter_board_gap_w"]),
        "{{VAL_CL}}": str(cfg["inter_board_gap_l"]),
        "{{VAL_SW}}": str(cfg["inter_single_gap_w"]),
        "{{VAL_SL}}": str(cfg["inter_single_gap_l"]),
        "{{VAL_KERF}}": str(cfg["kerf_allowance"]),
        "{{VAL_LIMIT}}": str(limit),
        "{{STAR_BADGE}}": ("Highest PCBs per Jumbo shown with ★" if max_pcbs_jumbo is not None else ""),
        "{{RESULTS_MESSAGE}}": results_message,
        "{{RESULTS_ROWS}}": results_rows,
        "{{RESULTS_TABLE_ATTRS}}": table_attrs,
    }

    for k, v in repl.items():
        html = html.replace(k, v)
    return html

# ------------------------------ WSGI Handler ---------------------------------

def app(environ, start_response):
    try:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "") or "/"
        if path in (f"/{ICON_FILENAME}", "/favicon.ico"):
            return _serve_static_asset(ICON_PATH, "image/png", method, start_response)

        if path == f"/static/{CSS_FILENAME}":
            return _serve_static_asset(CSS_PATH, "text/css; charset=utf-8", method, start_response)

        if method == "POST":
            size = int(environ.get("CONTENT_LENGTH", "0") or 0)
            body = environ["wsgi.input"].read(size).decode("utf-8") if size > 0 else ""
            qs = parse_qs(body)
        else:
            qs = parse_qs(environ.get("QUERY_STRING", ""))

        cfg = parse_cfg(qs)

        # Compute across all panel styles
        all_rows: List[Dict] = []
        enabled_sets = {letter for letter in "ABCDE" if cfg.get(f"include_set_{letter}", False)}
        for style, (pw, pl) in PANEL_OPTIONS.items():
            # Only evaluate panel styles whose set letter is enabled in the UI.
            if style[:1].upper() not in enabled_sets:
                continue
            all_rows.extend(enumerate_layouts(cfg, pw, pl, style))
        # Sort by PCBs per Jumbo desc, then utilization/objective for stability
        all_rows.sort(key=lambda r: (-r["pcbs_per_jumbo"], -r["utilization"], r["objective_key"]))
        all_rows = deduplicate_rows(all_rows)

        html = page(cfg, all_rows)
        data = html.encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"),
                                  ("Content-Length", str(len(data)))])
        return [data]
    except Exception as e:
        msg = f"<pre>{escape(repr(e))}</pre>"
        data = msg.encode("utf-8")
        start_response("500 Internal Server Error", [("Content-Type", "text/html; charset=utf-8"),
                                                     ("Content-Length", str(len(data)))])
        return [data]

# --------------------------------- Main --------------------------------------

if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "8080"))
    with make_server(host, port, app) as httpd:
        print(f"Serving on http://{host}:{port}")
        httpd.serve_forever()
