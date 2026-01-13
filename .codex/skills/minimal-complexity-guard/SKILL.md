---
name: minimal-complexity-guard
description: Enforces strict complexity budgets on code and workflows by reviewing structural size and cognitive load. This skill NEVER modifies code. It only evaluates whether approved changes exceed human-manageable complexity limits and requires explicit human authorization for any complexity expansion. Triggered by: complexity guard, complexity budget, structure limit, file too large, module too complex, flow too many states, keep code understandable.
---

# minimal-complexity-guard

## Purpose
Prevent structural complexity from exceeding human cognitive limits.

This skill acts as a **complexity firewall** between review and execution:
- It does not write code.
- It does not propose refactors.
- It only decides whether a structure is allowed to exist within defined complexity budgets.

Its output is a **PASS / FAIL / REQUIRE EXCEPTION** decision.

## Non-negotiable rules
- The agent MUST NOT modify any code.
- The agent MUST NOT generate patches, diffs, or drop-in code.
- The agent MUST NOT suggest architectural redesigns unless a budget is violated.
- All evaluations are structural, not functional.
- Behavior preservation is assumed and not re-evaluated here.
- Complexity expansion always requires explicit human approval.

## Complexity budgets

These are hard ceilings unless explicitly overridden:

1. Module concept budget  
   - A module may contain **at most 3 core concepts**.
   - A concept is defined as:
     - a distinct responsibility
     - a distinct domain concern
     - or a distinct behavioral role

2. File size budget  
   - A single file may contain **at most 200 lines of code (LOC)**, excluding:
     - blank lines
     - comments
     - license headers

3. Flow state budget  
   - Any state machine or implicit process flow may contain **at most 5 states**.
   - A state is:
     - a mode where behavior changes
     - or a conditional branch that alters global behavior

4. Exception policy  
   - Any violation requires one of:
     - structural reduction (split / delete / compress)
     - or explicit human authorization to exceed the limit

The agent must never silently accept a violation.

## Inputs required
- Target files or modules
- Approved necessity candidates (if coming from minimal-necessity-review)
- Scope boundaries:
  - Which files are in review
  - Whether new code is expected (usually NO)

If scope is ambiguous, ask for clarification.

## Output format

The agent MUST output:

1. Scope Summary
Target files:
Approved candidates:
Budgets applied:

2. Budget Evaluation
For each file:
File: <path>
LOC:
* Measured:
* Budget:
* Status: PASS / FAIL
Concept count:
* Identified concepts:
  1.
  2.
  3.
* Budget:
* Status: PASS / FAIL
Flow states:
* Identified states:
  1.
  2.
  3.
* Budget:
* Status: PASS / FAIL

3. Global Verdict
One of:
- PASS  
  Structure is within all budgets. Safe to proceed to drop-in execution.
- FAIL – Reduction Required  
  At least one budget is exceeded. Provide minimal structural reduction suggestions.
- FAIL – Human Exception Required  
  The structure cannot be reduced without breaking requirements. Explicit authorization needed.

4. If FAIL – Reduction Required
Only suggest:
- splitting files
- deleting unused blocks
- separating responsibilities
Do NOT write code.  
Do NOT provide implementation steps.

5. Human Decision Gate
Reply using one of:
* PASS
* APPROVE structural reduction plan A
* APPROVE exception for <file>: <reason>
* OVERRIDE budgets: <explicit new limits>

No execution happens without this.

## Prohibited actions
The agent MUST NOT:
- change code
- suggest micro-optimizations
- refactor logic
- propose clever abstractions
- introduce frameworks
- merge business logic

This skill only guards structural limits.

## Ethos
- Complexity is a liability.
- Every structure must justify its size.
- Human understanding is the ultimate budget.
- If something cannot fit the budget, it must request permission to exist.

## Example usage

Use skill: minimal-complexity-guard
Scope:
* Files: ali_email.py, ali_fetch.py
* Approved candidates: C1, C3
Mode:
* REVIEW ONLY

Expected output:
- Budget evaluation
- PASS or FAIL
- Explicit decision gate

