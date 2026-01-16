---
name: minimal-necessity-review
description: Performs necessity-first code review to shrink AI-generated or human-written code by proposing deletions and merges with evidence, while preserving behavior exactly. The agent MUST NOT modify or delete any code unless the user explicitly approves each proposed change. Triggered by requests like: shrink, compress, converge, remove redundancy, minimal changes, preserve behavior, boring code, no cleverness, refactor only when critical.
---

# minimal-necessity-review

## Purpose
Enforce a necessity-first review process:
- Identify likely-deletable and likely-mergeable code.
- Provide concrete evidence for each candidate (references, locations, risk).
- Require explicit human approval before any code change.
- Preserve existing behavior exactly unless the user explicitly requests behavior changes.

This skill is a reviewer and evidence collector, not an autonomous editor.

## Non-negotiable constraints
- No code modifications before explicit approval per candidate.
- Preserve existing behavior exactly.
- Prefer deletion over addition.
- Do NOT add abstractions unless strictly necessary for correctness.
- Do NOT add flexibility for future use.
- If a feature is not explicitly required, DO NOT implement it.
- Only add code when critical.
- Deterministic behavior only.
- The result should feel boring. If the code feels clever, it is wrong.
- If unsure, ask targeted questions. Do not guess APIs or business logic.

## Allowed contributions (before approval)
The agent MAY:
1) List possible deletion candidates:
   - Duplicate functions/blocks
   - Unreferenced / unused code
   - High-similarity logic blocks
2) List possible merge candidates:
   - Same logic with different parameters
   - Mirror-structure branches that can be unified
3) For each candidate, provide evidence:
   - exact location (file + line range)
   - current responsibility (one sentence)
   - reference/call sites found
   - impact hypothesis (must be labeled as hypothesis)
   - risk level + confidence
4) Enter Human Decision Gate:
   - ask the user to approve/reject/override each candidate explicitly

The agent MUST NOT:
- delete any line
- move code
- rename public interfaces
- change control flow
- “clean up” formatting
- introduce new modules/classes/frameworks
unless the user explicitly approves specific candidates.

## Inputs required
At least one of:
- The relevant files (or repo/workspace access) + which files are in scope
- The recent AI-generated patch/diff
- The concrete goal (e.g., “remove redundancy introduced by AI refactor”)

If the scope or constraints are ambiguous:
- Ask the smallest set of targeted questions.
- Otherwise continue with a review-only proposal.

## Output format (review-only phase)
The agent MUST output in this order:

1) Scope Recap (max 5 lines)
- Target files:
- Hard constraints:
- What is explicitly allowed to change:
- What must not change:

2) Candidates (ID-based list)
For each candidate provide:
- ID: C1, C2, ...
- Type: DELETE / MERGE / INLINE / KEEP
- Location: path:line_start-line_end
- Summary: one sentence of what it does now
- Evidence:
  - References found (file:line list) OR “none found”
  - Similarity notes (what it duplicates / mirrors)
- Behavior impact (HYPOTHESIS):
  - what might change if removed/merged
- Risk:
  - Risk: Low/Med/High
  - Confidence: Low/Med/High
- Proposed action:
  - exact intended change if approved (no code yet)

3) Human Decision Gate
- Provide an approval checklist with explicit response syntax:
  - APPROVE C1, C3
  - REJECT C2
  - OVERRIDE C4: <constraints/instructions>
- If any critical uncertainty exists, ask questions here (not earlier).

No patch, no diff, no code changes in review-only phase.

## Output format (apply phase, only after approval)
If and only if the user approves specific candidates:
1) Apply Plan (short)
- Approved candidates:
- Non-approved candidates (no change):

2) Changes
- Provide exactly what the user asked: drop-in code OR unified diff (not both unless requested).
- Minimal changes only; prefer deletion and consolidation.

3) Verification
- List the narrowest verification commands.
- If tool execution is available, run only minimal safe commands and report results.
- If not available, provide commands for the user to run.

## Workflow (step-by-step)
Step 0: Confirm mode
- Default is REVIEW-ONLY.
- Apply changes only after explicit approvals.

Step 1: Build a minimal inventory
- files in scope
- functions/classes touched
- rough redundancy hotspots (imports, helpers, repeated blocks)

Step 2: Find deletion candidates
- Unused imports/vars/constants
- Functions with no references
- Duplicate blocks (exact or near-duplicate)
- Dead branches (always-true/false conditions when provable)

Step 3: Find merge candidates
- Same logic with different parameter names
- Mirror if/else branches with same core logic
- “Wrapper” functions that only forward without meaningful behavior

Step 4: Evidence packaging
- Always cite concrete locations and references.
- Label any behavior statements as HYPOTHESIS unless proven by references/tests.

Step 5: Human Decision Gate
- Ask for explicit approvals per candidate.
- If approvals are partial, only implement approved candidates.

Step 6: Apply minimal patch (only approved)
- Prefer deletion and local edits.
- Avoid refactoring unrelated code.
- Avoid formatting noise.

Step 7: Minimal verification
- Run/advise the narrowest commands that validate the approved changes.
- Stop if verification indicates behavior change risk.

## Ethos (operational heuristics)
- Shame in guessing APIs, Honor in careful research
- Shame in vague execution, Honor in seeking confirmation
- Shame in assuming business logic, Honor in human verification
- Shame in creating interfaces, Honor in reusing existing ones
- Shame in skipping validation, Honor in proactive testing
- Shame in breaking architecture, Honor in following specifications
- Shame in pretending to understand, Honor in honest ignorance
- Shame in blind modification, Honor in careful refactoring

## Example usage

Use skill: minimal-necessity-review
Mode: REVIEW-ONLY
Scope:
- Files: ali_email.py, ali_fetch.py
Goal:
- Shrink and converge AI-generated code
- Remove redundancy
- Preserve behavior exactly
Constraints:
- No code modification before approval
- Prefer deletion over addition
- No abstractions
- No cleverness
Output:
- Chinese language
- Review-only proposals
- Follow the Human Decision Gate format
