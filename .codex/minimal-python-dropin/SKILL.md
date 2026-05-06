---
name: minimal-python-dropin
description: Minimal correct Python fix or micro-refactor. Preserve behavior, prefer deletion, avoid new helpers/layers, and output direct drop-in code when requested. Use for: drop-in code, minimal changes, preserve functionality, remove duplicate code, no clever code, boring fix.
---

# minimal-python-dropin

## Objective

Make the smallest correct change to existing Python code.

Default goal:

- Preserve existing behavior exactly.
- Change only what the user explicitly requested.
- Prefer deletion, inlining, and simplification before adding code.
- Avoid new abstractions, new interfaces, and future-proofing.
- Output direct drop-in code when requested.

The result should feel boring, obvious, and easy to verify.

## Use when

Use this skill for:

- Fixing a concrete bug, error trace, import error, NameError, or failing behavior.
- Small behavior-preserving refactor.
- Removing duplicate or redundant code.
- Producing complete drop-in replacement code with minimal internal changes.

Do not use this skill for:

- Redesigning architecture.
- Adding optional features.
- Building frameworks, plugin systems, registries, factories, or generic engines.
- Reformat-only rewrites that create noisy diffs.

## Required inputs

The user must provide at least one of:

- Relevant file content or accessible repo/workspace context.
- Exact error trace or failing behavior.
- Exact requested change and invariants.

If the requirement is ambiguous, ask the smallest number of targeted questions. Do not guess business logic.

## Hard constraints

- Preserve existing behavior exactly unless the user explicitly requests a change.
- Implement only the requested change.
- Make the smallest correct edit.
- Prefer deleting code over adding code.
- Do not add abstractions unless required for correctness.
- Do not add flexibility for future use.
- Do not change public interfaces unless explicitly requested.
- Do not rename broadly unless it clearly reduces confusion and is safe.
- Do not introduce new dependencies.
- Do not introduce new test frameworks.
- No clever code.
- No optional features.
- No future-proofing.

## Full drop-in rule

When the user asks for complete drop-in code:

- Return the complete updated file.
- Do not treat “drop-in” as permission to rewrite freely.
- The internal change must still be the smallest safe edit.
- Preserve imports, structure, naming, and flow unless changing them directly reduces duplication or fixes the issue.

## Helper budget rule

Do not add helper functions by default.

A new helper is allowed only when all are true:

- It replaces at least two duplicated code blocks.
- It has more than one real caller.
- It reduces repeated logic or total complexity.
- It does not create a new abstraction layer.
- It does not prepare for future features.

Disallowed helper patterns:

- One-call wrapper helper.
- Naming-only helper.
- Future-extension helper.
- Helper that only moves code without reducing complexity.
- Helper that hides simple linear control flow.
- Helper added before deleting existing duplication.

Preferred order:

1. Delete dead or duplicate code.
2. Inline trivial wrappers.
3. Merge equivalent branches.
4. Reuse existing helpers.
5. Add a new helper only as last resort.

## Refactor metrics gate

For cleanup, refactor, simplify, reduce duplicate, or “less clumsy” tasks, report before/after metrics before final delivery.

Required metrics:

- Changed files count.
- Non-empty non-comment LOC before / after.
- Net LOC delta.
- `def` count before / after.
- `class` count before / after.
- Helper-like function count before / after.
- New helpers added.
- Helpers removed or inlined.

Acceptance rules:

- A cleanup/refactor should normally reduce LOC, reduce duplicate branches, flatten the call graph, or reduce helper count.
- If LOC increases, explain why the increase is required.
- If helper count increases and LOC also increases, the refactor failed unless required for correctness.
- If behavior was not added but code became larger, perform one more compression pass before final output.

## Workflow

1. Read only the relevant files.
2. State the exact required change in one sentence.
3. Identify invariants:
   - what must not change;
   - what behavior must remain identical.
4. Find the smallest edit location.
5. Delete or inline redundancy before adding code.
6. Implement the smallest correct change.
7. For refactor tasks, run the metrics gate.
8. Validate with the narrowest real command available:
   - existing tests if present;
   - otherwise import check, syntax check, or minimal execution path.
9. Return final drop-in code or patch as requested.

## Final self-check

Before final answer, verify:

- The exact change can be explained in one sentence.
- Existing behavior is preserved.
- The edit location is minimal.
- Redundancy was deleted before code was added.
- No unnecessary helper was added.
- No new abstraction or interface was added.
- No optional feature was added.
- Validation was run or clearly marked as not run.
- Output format matches the user request.

For refactor tasks, also verify:

- LOC did not increase without reason.
- Helper count did not increase without reason.
- The call graph is not deeper for no benefit.
- The new version is easier to read, not merely more structured.

## Output format

Default final output:

```text
Changed files:
- <file>

Change:
- <one-sentence summary>

Metrics:
- LOC: <before> -> <after> (<delta>)
- def: <before> -> <after>
- class: <before> -> <after>
- helpers: <before> -> <after>
- changed files: <count>

Validation:
- <command>: <result>

Assumptions:
- None