# MIGRATION_MAP

## 1) Entry Points And Request Flow

- Entry file: `LLE/main.php`
- Runtime model: single script handling both initial page render and POST submission.
- HTTP methods used:
1. `GET`: render form with session-backed defaults/previous values.
2. `POST` with `calculate_params`: validate/store inputs in session, query DB, run calculations, render results.

### Parameters read (POST)

1. `calculate_params`
- Role: submit trigger (`isset($_POST['calculate_params'])`)
- Required: yes for processing branch

2. `target_cct`
- Validation: `isset && !empty && is_numeric`
- Stored as float in session
- Error text: `Please select a Target CCT from the dropdown`

3. `target_lumen`
- Validation: `isset && is_numeric && > 0`
- Stored as float
- Error text: `Target Luminaire Lumen Output must be a positive number`

4. `target_efficacy`
- Validation branch runs only if `isset`
- Pre-normalization: `str_replace(',', '.', value)`
- Validation: `is_numeric && > 0`
- Stored as float
- Error text: `Target Luminaire Efficacy must be a positive number (lm/W)`

5. `optical_transmission`
- Validation: `isset && is_numeric && 1..100`
- Stored as float
- Error text: `Luminaire Optical Transmission Rate must be between 1-100 percent`

6. `power_efficiency`
- Validation: `isset && is_numeric && 1..100`
- Stored as float
- Error text: `Power Supply Efficiency must be between 1-100 percent`

7. `junction_temp`
- Validation: `isset && is_numeric`
- Stored as float
- Error text: `Junction Temperature must be a valid number (°C)`

8. `v_chain_max`
- Validation: `isset && is_numeric && > 0`
- Stored as float
- Error text: `Maximum LED Chain Voltage must be a positive number (V)`

9. `smt_cost_rmb`
- Validation: `isset && is_numeric && >= 0`
- Stored as float
- Error text: `SMT Cost in RMB must be a positive number or zero`

10. `usd_rate`
- Validation: `isset && is_numeric && > 0`
- Stored as float
- Error text: `USD Exchange Rate must be a positive number greater than 0`

11. `target_cri`
- Validation: `isset && !empty && is_numeric`
- Stored as float
- Error text: `Please select a Target CRI from the dropdown`

### Session-backed defaults for display

1. `target_cct`: `''`
2. `target_lumen`: `''`
3. `target_efficacy`: `''`
4. `junction_temp`: `65`
5. `v_chain_max`: `50`
6. `smt_cost_rmb`: `0.01`
7. `usd_rate`: `7.00`
8. `optical_transmission`: `80`
9. `power_efficiency`: `85`
10. `target_cri`: `''`

### Required vs optional behavior notes

- Query/calculation path executes only when:
1. method is POST
2. `calculate_params` exists
3. validation_errors is empty
4. `target_cct` and `target_cri` are non-empty in session
- `target_efficacy` has no explicit `else` error if field missing (validation branch conditional on `isset` only).

## 2) UI Structure

- Single page HTML with inline CSS.
- Title: `LLE Solution Development Tool ... Phase 10`
- Sections:
1. Parameter form section
2. Conditional validation errors block
3. Conditional success message block
4. Conditional query result section
5. Always-visible DB status box
6. Conditional DB-failed explanation section

### Form fields and controls

- Form method: `POST`, action empty string.
- Submit button: `name="calculate_params"`, text `Store Parameters & Prepare Calculations`.
- Fields (names preserved):
1. `target_cct` (`select`, required)
2. `target_cri` (`select`, required)
3. `target_lumen` (`number`, required)
4. `optical_transmission` (`number`, required)
5. `power_efficiency` (`number`, required)
6. `target_efficacy` (`number`, required)
7. `junction_temp` (`number`, required)
8. `v_chain_max` (`number`, required)
9. `smt_cost_rmb` (`number`, required)
10. `usd_rate` (`number`, required)

### Conditional rendering rules

1. Validation list: shown only when form submitted and `validation_errors` non-empty.
2. Success message: shown only when `success_message` non-empty.
3. Candidate/results section: shown only when `query_executed` true.
4. Inside results:
- If `candidate_count > 0`, show result table + configuration tables + info blocks.
- Else, show no-models-found message.
5. DB status box always rendered with class `success` or `error`.
6. DB-failure explainer section rendered when `connection_status != "Success"`.

## 3) Algorithm Blocks

### A) Polynomial helper functions

1. `calculateFIV(if_value, row)`
- Inputs: `if_value`, row coefficients `FIV_0..FIV_6`
- Output: FIV polynomial value (absolute Vf at current)
- Fallback: `1.0` on exception

2. `calculateFIVDerivative(if_value, row)`
- Inputs: `if_value`, `FIV_1..FIV_6`
- Output: derivative of FIV polynomial
- Fallback: `0.0`

3. `calculateFIL(if_value, row)`
- Inputs: `if_value`, `FIL_0..FIL_6`
- Output: FIL polynomial value
- Special rule: if computed FIL == 0, return `1.0`
- Fallback: `1.0`

4. `calculateFILDerivative(if_value, row)`
- Inputs: `if_value`, `FIL_1..FIL_6`
- Output: derivative of FIL polynomial
- Fallback: `0.0`

5. `calculateObjectiveFunction(if_value, k_eta, k_phi, row)`
- Formula: `k_eta*(if_value/1000)*FIV(if_value) - k_phi*FIL(if_value)`
- Fallback: `0.0`

6. `calculateObjectiveFunctionDerivative(if_value, k_eta, k_phi, row)`
- Formula: `k_eta*(FIV/1000 + (if_value/1000)*FIV') - k_phi*FIL'`
- Guard: if abs(derivative) < `1e-10`, return `1e-10`
- Fallback: `1e-10`

7. `calculateVf(target_if, target_tj, row)`
- Step 1: `vf_at_25C = FIV(target_if)`
- Step 2: `vf_factor = FTV(target_tj)` polynomial (`FTV_0..FTV_6`)
- Step 3: `vf_final = vf_at_25C * vf_factor`
- Fallback: `3.0`

8. `calculateVfWithDebug(target_if, target_tj, row)`
- Same as `calculateVf` plus debug keys:
1. `vf_final`
2. `vf_at_25C`
3. `fiv`
4. `ftv`
5. `vf_test` fixed string `N/A`
- Fallback returns fixed default debug dict.

### B) Derived requirement block

- Inputs: validated session values
1. `target_led_lumen = target_lumen / (optical_transmission/100)`
2. `target_led_efficacy = target_efficacy / ((optical_transmission/100)*(power_efficiency/100))`

### C) Candidate row processing block

For each `LED_CoE` row matching CCT+CRI:

1. `lumen_factor = FTL(target_tj)` polynomial (`FTL_0..FTL_6`) fallback 1.0
2. `vf_factor = FTV(target_tj)` polynomial (`FTV_0..FTV_6`) fallback 1.0
3. `k_eta = target_led_efficacy * vf_factor` if target_led_efficacy > 0 else 0
4. `k_phi = lm_test * lumen_factor` if lm_test > 0 else 0

### D) Newton-Raphson block

- Init:
1. `target_if = 10.0`
2. `tolerance = 0.0001`
3. `max_iterations = 100`
4. `iteration_count = 0`
5. `converged = false`

- Preconditions: `k_eta > 0`, `k_phi > 0`, `If_max > 0`
- Iteration:
1. compute `f`
2. compute `f_derivative`
3. if `abs(f) < tolerance`: converged and break
4. `temp_if = target_if - (f/f_derivative)`
5. if out of bounds `<0` or `>If_max`: `target_if = target_if + 10`
6. else `target_if = temp_if`
7. safety cap: if `target_if > If_max` set to `If_max` and break
- On iteration exception: `target_if = 50.0`, `converged = false`
- If preconditions fail: `target_if = 50.0`, `converged = false`

### E) Lumen and LED count block

1. `fil_at_target_if = FIL(target_if)`
2. `lumen_at_25C_target_if = lm_test * fil_at_target_if`
3. `lumen_at_target_Tj_target_if = lumen_at_25C_target_if * lumen_factor`
4. `led_count = ceil(target_led_lumen / lumen_at_target_Tj_target_if)` when denominator > 0
5. On exception: all three set to 0

### F) Series/parallel configuration block (Phase 9)

For each candidate with `required_led_count > 0` and `target_if > 0`:

1. `vf_single` from `calculateVfWithDebug`
2. Iterate `P = 1..min(20, required_led_count)` and at most 10 accepted solutions
3. Start `led_count_working = required_led_count`, `led_add = 0`
4. Increment `led_count_working` until divisible by `P` (`led_add` tracks increments)
5. `S = led_count_working / P`
6. Require `S >= 2`
7. `V_chain = S * vf_single`
8. Keep solution only if `V_chain <= v_chain_max`
9. Sort solutions by:
- `S` descending
- `led_add` ascending
- `V_chain` descending

### G) Candidate presentation sorting blocks

1. Search results sorting (`sorted_candidates`): by first solution total USD cost ascending.
2. Configuration model sorting (`candidate_costs`): same rule.

### H) Table display formulas

1. `Fixture (lm) = Lm/LED * LED Count * optical_transmission/100`
2. `Input Power (W) = Fixture (lm) / target_efficacy`
3. `Total Current (mA) = target_if * P`
4. `Power (W) = V_chain * Total Current / 1000`
5. `LED + SMT (USD) = total_leds*USD + total_leds*smt_cost_rmb/usd_rate`
6. `LED + SMT (RMB) = total_leds*RMB + total_leds*smt_cost_rmb`

### Side effects

- Session writes for validated inputs.
- DB reads only (no INSERT/UPDATE/DELETE).

## 4) Database Behavior

- DB type in PHP: MySQL via `mysqli`.
- Connection constants in source:
1. host `localhost`
2. user `baltechind_kenny`
3. password `Kenny123`
4. database `baltechind_grow`

### SQL statements executed

1. `DESCRIBE LED_CoE`
2. `SELECT DISTINCT CCT FROM LED_CoE WHERE CCT IS NOT NULL ORDER BY CCT ASC`
3. `SELECT DISTINCT CRI FROM LED_CoE WHERE CRI IS NOT NULL ORDER BY CRI ASC`
4. `SELECT * FROM LED_CoE WHERE CCT = ? AND CRI = ?` (prepared statement)

### Table and fields used

- Table: `LED_CoE`
- Fields directly used in logic/UI:
1. `Model`
2. `CCT`
3. `CRI`
4. `lm_test`
5. `If_max`
6. `USD`
7. `RMB`
8. `Quote`
9. `FIV_0..FIV_6`
10. `FIL_0..FIL_6`
11. `FTL_0..FTL_6`
12. `FTV_0..FTV_6`

### Role of `LED_CoE.sql`

- Schema + data dump for database `baltechind_grow` table `LED_CoE`.
- Used as source dataset for CCT/CRI options and candidate calculations.
- Contains table DDL, inserts, primary key/auto-increment definitions.

## 5) External Dependencies

1. Session: PHP `$_SESSION` storage.
2. DB server: MySQL/MariaDB reachable by configured credentials.
3. Static asset: `LLE.png` (favicon via `<link rel="icon" ...>`).
4. No file reads/writes in runtime logic.
5. No cookies manually set/read (beyond session mechanism).
6. No environment variable usage in source.

## 6) Numeric Conventions

1. Polynomial coefficient order is ascending by power:
- constant term `_0`
- linear `_1`
- ...
- sixth-order `_6`
2. Current unit in formulas: `mA`; conversion to `A` when multiplied by voltage uses `/1000`.
3. Temperature unit: `°C`.
4. Voltage unit: `V`.
5. Luminous flux unit: `lm`.
6. Efficacy unit: `lm/W`.
7. Rounding/formatting:
- `number_format(..., 0|1|2)` for table output
- `ceil` for LED count
- numeric display fallback text `N/A` when value conditions fail
8. Newton-Raphson control constants:
- tolerance `0.0001`
- max iterations `100`
- derivative floor `1e-10`
- fallback current `50.0`
