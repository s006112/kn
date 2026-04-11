---
name: multi-layer-alignment
description: enforce consistency between contract, flow, and script layers in the same directory. use when updating system design, refactoring logic, or ensuring documentation and code alignment across layered architecture.
---

# Overview

This skill maintains alignment across three layers:

- contract (rules, constraints, invariants)
- flow (decision order and execution structure)
- script (implementation)

It supports:
- detecting differences
- classifying change type
- scanning derived documents
- generating aligned updates

---

# When to Use This Skill

Invoke this skill when ANY of the following happens:

- contract file updated
- flow file updated
- script logic modified
- decision order changed
- new branch or condition introduced
- refactor affecting execution path

Do NOT invoke when:
- formatting only changes
- comment-only updates
- non-behavioral renaming

# Core Rules

## 1. Source of Truth

Always determine the source layer first:

- contract → default recommended
- flow → only when redefining execution semantics
- script → only when implementation defines reality

Never treat all three layers as equal.

---

## 2. File Discovery

Scan files in the same directory:

- contract*.md → contract layer
- flow*.md → flow layer
- *.py → script layer

Ignore folder hierarchy. Only same-level files are considered.

---

## 3. Change Classification

Before making any update, classify the change:

### semantic_change
- modifies rules
- modifies allowed states
- modifies decision order
- modifies constraints or boundaries

### implementation_change
- refactor code
- change helper functions
- change logging or structure
- no change to behavior semantics

### expression_change
- improve wording
- reorganize flow
- clarify explanation
- no change to behavior

---

## 4. Alignment Strategy

### semantic_change
- update contract
- regenerate flow from contract
- update script to match contract

### implementation_change
- update script
- update flow only if execution order or observable behavior changed
- do not modify contract

### expression_change
- update flow only
- do not modify contract or script

---

## 5. Alignment Execution Workflow

Always follow this order:

### Step 1 — Identify Source
Determine which layer is the source of change.

### Step 2 — Locate Impacted Blocks
Map change to one of:

- state model
- decision order
- transition rules
- boundary conditions
- observable behavior

### Step 3 — Scan Derived Layers
Compare:

- contract vs flow
- contract vs script
- flow vs script

### Step 4 — Detect Drift


### Drift Detection Output

Always classify drift as:

- missing_branch
- extra_branch
- order_mismatch
- boundary_violation
- hidden_logic

Additionally output:

severity: critical / major / minor
layer: contract / flow / script

### Step 5 — Generate Patch

Rules:

- minimal change
- do not introduce new semantics
- preserve naming consistency

### Step 6 — Validate

Checklist:

- all accepted paths exist in script
- no forbidden paths exist in script
- flow order matches actual execution
- contract covers all branches

---

## 6. Output Format

Always output:

### A. Change Classification

type: semantic / implementation / expression
source: contract / flow / script


### B. Affected Blocks


- decision order
- anchor boundary
- pair keep logic


### C. Alignment Result

contract: updated / unchanged
flow: updated / unchanged / stale
script: updated / unchanged / stale

Note:
"stale" means this layer is not aligned with source-of-truth and requires future update.

### D. Direct Drop-in Patches

Only include modified sections.

---

## 7. Constraints

* never invent new behavior not defined in source
* never silently fix logic without surfacing reasoning
* never treat flow as source unless explicitly stated
* never modify all three layers blindly

---

# Advanced Mode (optional)

If multiple contracts exist:

* treat each contract independently
* do not merge domains unless explicitly instructed

If ambiguity exists:

* prefer stricter contract
* avoid expanding behavior
