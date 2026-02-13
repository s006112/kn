# VERIFY

## 1) Run Instructions

1. `cd /workspaces/kn/LLE`
2. `pip install -r requirements.txt`
3. Ensure MySQL has database `baltechind_grow` and table/data from `LED_CoE.sql`.
4. `python app.py`
5. Open `http://127.0.0.1:5000/`

## 2) Representative Request Scenarios (10+)

1. Initial GET (no prior session)
- Request: `GET /`
- Expect: form shown with defaults (`junction_temp=65`, `v_chain_max=50`, `smt_cost_rmb=0.01`, `usd_rate=7`, `optical_transmission=80`, `power_efficiency=85`), no result section.

2. POST missing `target_cct`
- Request: `POST /` with `calculate_params=1` and other fields valid
- Expect: validation error `Please select a Target CCT from the dropdown`.

3. POST invalid `target_lumen` (0)
- Request: `target_lumen=0`
- Expect: `Target Luminaire Lumen Output must be a positive number`.

4. POST invalid `target_efficacy` (comma format valid)
- Request: `target_efficacy=120,5`
- Expect: accepted; stored as `120.5`; success message shown when all fields valid.

5. POST invalid `optical_transmission` (>100)
- Request: `optical_transmission=101`
- Expect: `Luminaire Optical Transmission Rate must be between 1-100 percent`.

6. POST invalid `power_efficiency` (<1)
- Request: `power_efficiency=0.5`
- Expect: `Power Supply Efficiency must be between 1-100 percent`.

7. POST invalid `usd_rate` (<=0)
- Request: `usd_rate=0`
- Expect: `USD Exchange Rate must be a positive number greater than 0`.

8. POST valid but no matching CCT/CRI
- Request: valid form with values not present in DB
- Expect: query section appears; message `No LED models found matching CCT: ... and CRI: ...`.

9. POST valid with matching CCT/CRI
- Request: valid existing pair from dropdowns
- Expect: candidate table populated; configuration section shown; sorting by first configuration USD cost ascending.

10. Numeric display verification
- Check: `Fixture (lm)` integer format, `Input Power (W)` one decimal, cost USD/RMB two decimals, `If (mA)` one decimal.
- Expect: formatting equivalent to PHP `number_format` usage.

11. DB failure behavior
- Stop DB or use wrong credentials.
- Expect: immediate plain response `Connection failed: ...` (PHP-equivalent early `die`).

## 3) Expected Outputs / Key Fields

- Presence of exact UI labels/text from PHP.
- Error strings exactly preserved.
- Computed fields present:
1. `LED Count`
2. `Lm/LED`
3. `If (mA)`
4. `LED + SMT (USD)`
5. `P/S` configuration rows

## 4) Comparison Method

1. Baseline:
- Run original PHP version against same DB and input set.
2. Capture:
- Save HTML for each scenario (`curl` or browser save page).
3. Compare:
- HTML/text diff for labels/messages/sections.
- Numeric diff for key numeric cells (`Fixture`, `Input Power`, `LED Count`, `If`, costs, `V_chain`) using exact string comparison.

## 5) Coverage Checklist

- Default path: Scenario 1
- Missing params: Scenario 2
- Invalid params: Scenarios 3, 5, 6, 7
- DB-driven paths: Scenarios 8, 9, 11
- Main computation paths: Scenarios 9, 10

## 6) Pass/Fail Criteria

Pass when all are true:
1. UI text/labels/flow match PHP output.
2. Validation and success messages match exactly.
3. Candidate/config sections appear under same conditions.
4. Sorting order matches (`USD` cost asc, then solution sort rules).
5. Numeric fields match within display precision (same formatted strings).
6. DB connect failure path matches PHP early-fail behavior.

Fail if any mismatch above is observed.
