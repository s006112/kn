# Alignment Execution Prompt

Use skill: multi-layer-alignment

## Input

Source of truth:
- contract / flow / script

Changed file:
- <filename>

Change summary:
- <what changed>

---

## Task

1. classify change type
2. locate affected contract blocks
3. scan all related files in same directory:
   - contract*.md
   - flow*.md
   - *.py
4. detect drift across layers
5. generate minimal patch
6. validate alignment

---

## Output Format

### 1. Change Classification
type:
source:

### 2. Affected Blocks
- state model:
- decision order:
- transition rules:
- boundary:
- observable behavior:

### 3. Drift Detected
- missing_branch:
- extra_branch:
- order_mismatch:
- boundary_violation:
- hidden_logic:

### 4. Patch (Direct Drop-in)

#### file: <filename>
<only modified section>

### 5. Alignment Result
contract:
flow:
script:

### 6. Notes