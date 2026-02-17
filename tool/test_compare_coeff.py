import sqlite3
import json
import numpy as np
import importlib.util

DB = "led_coe_fallback.sqlite3"
BUNDLE = "CoeffBundle.json"
MODEL = "STW8A2PD-H0"

# load algorithm.py
spec = importlib.util.spec_from_file_location("algorithm", "algorithm.py")
algo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(algo)

# load original row
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
row = dict(con.execute(
    "SELECT * FROM LED_CoE WHERE Model=? LIMIT 1",
    (MODEL,)
).fetchone())
con.close()

# clone and override
new_row = row.copy()
bundle = json.load(open(BUNDLE))

def apply_curve(prefix):
    coeff = bundle[prefix]["coeff_power"]
    coeff = list(reversed(coeff))  # reverse order

    for k in range(7):
        new_row[f"{prefix}_{k}"] = 0.0

    for k in range(len(coeff)):
        new_row[f"{prefix}_{k}"] = coeff[k]

for p in ["FIV","FIL","FTL","FTV"]:
    apply_curve(p)

# sampling grid
ifs = np.linspace(0,300,301)
tjs = np.linspace(25,125,101)

# FIV
vf_old = np.array([algo.calculateFIV(float(i), row) for i in ifs])
vf_new = np.array([algo.calculateFIV(float(i), new_row) for i in ifs])
print("ΔFIV max =", float(np.max(np.abs(vf_new - vf_old))))

# FIL
fil_old = np.array([algo.calculateFIL(float(i), row) for i in ifs])
fil_new = np.array([algo.calculateFIL(float(i), new_row) for i in ifs])
print("ΔFIL max =", float(np.max(np.abs(fil_new - fil_old))))

# FTV
def poly(prefix,x,r):
    return sum(r[f"{prefix}_{k}"]*(x**k) for k in range(7))

ftv_old = np.array([poly("FTV",float(t),row) for t in tjs])
ftv_new = np.array([poly("FTV",float(t),new_row) for t in tjs])
print("ΔFTV max =", float(np.max(np.abs(ftv_new - ftv_old))))

# Vf @ Tj
TJ = 65
vfT_old = vf_old * poly("FTV",TJ,row)
vfT_new = vf_new * poly("FTV",TJ,new_row)
print("ΔVf@65C max =", float(np.max(np.abs(vfT_new - vfT_old))))
