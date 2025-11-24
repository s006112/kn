# quote.py
import json
import socket
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from template import HTML_TEMPLATE

def column_config():
    return [
        {"key": "item",       "label": "Item",        "editable": False, "type": "text",   "precision": 0, "align": "left"},
        {"key": "remarks",    "label": "Remarks",     "editable": True,  "type": "text",   "precision": 0, "align": "left"},
        {"key": "unit_cost",  "label": "Unit Cost",   "editable": True,  "type": "number", "precision": 4, "align": "right", "min": 0},
        {"key": "qty",        "label": "Qty",         "editable": True,  "type": "number", "precision": 4, "align": "right", "min": 0},
        {"key": "line_total", "label": "Line Total",  "editable": False, "type": "number", "precision": 4, "align": "right"},
    ]

def load_defaults():
    rows = [
        ["Lamination 板材",       "Thickness, Cu, Material", 110,     1],
        ["Surface treatment 板材","OSP / 无铅喷锡 / ENIG / 银 / 锡", 28, 1],
        ["PTH",                   "TBD",                     0.0017, 30000],
        ["干膜",                   "TBD",                     12,      1],
        ["蚀刻",                   "TBD",                     3,       1],
        ["阻焊",                   "單面/双面",                 12,      1],
        ["丝印",                   "單面/双面",                 2,       1],
        ["污水处理",                "立方米",                    30,      1],
        ["污水处理电费",             "度",                       30,      0.2],
        ["CNC 钻孔",               "TBD",                     0.0034, 30000],
        ["开料",                   "TBD",                     8,       1],
        ["锣板 / 冲板",             "TBD",                     18,      1],
        ["电测",                   "TBD",                     1,       1],
        ["V-Cut",                 "TBD",                     0.5,     1],
        ["FQC",                   "TBD",                     0.5,     1],
        ["包装",                   "TBD",                     0.5,     1],
    ]
    out = []
    for r in rows:
        d = {"item": r[0], "remarks": r[1], "unit_cost": float(r[2]), "qty": float(r[3])}
        d["line_total"] = round(d["unit_cost"] * d["qty"], 6)
        out.append(d)
    return out

def calc_rules():
    return {"row_formula": "line_total = unit_cost * qty",
            "totals": ["subtotal = sum(line_total)", "grand_total = subtotal"]}


class QuoteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            self._serve_index()
        else:
            self._not_found()

    def _serve_index(self):
        cfg = {"columns": column_config(), "rules": calc_rules()}
        data = load_defaults()
        page = HTML_TEMPLATE.replace("{CFG_JSON}", json.dumps(cfg, ensure_ascii=False)) \
                            .replace("{DATA_JSON}", json.dumps(data, ensure_ascii=False))
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self):
        body = b"Not Found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def find_free_port(start=8000, max_tries=50):
    port = start
    for _ in range(max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    return 0

def run_server():
    port = find_free_port()
    if port == 0:
        raise RuntimeError("No free port found.")
    server = HTTPServer(("127.0.0.1", port), QuoteHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"Serving on {url}")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    server.serve_forever()

if __name__ == "__main__":
    run_server()
