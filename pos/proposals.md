# Proposals

Candidate rules extracted from real work but not yet stable.

Promotion rule:
- Promote only after repeated validation and explicit human approval.
- Merge or delete proposals that become redundant with stable assets.

## Proposal 001 — Prefer explicit dependency passing over hidden global state

Pattern:
- Hidden global state makes runtime ownership unclear.

Suggested rule:
- Prefer explicit dependency passing over module-level mutable state.

Criteria:
- A function can receive the needed object directly.
- Runtime state already exists in a visible scope.
- The global exists only for convenience.

Boundary:
- Temporary global state is acceptable for true singleton process interfaces or compatibility shims.
- If removing global state breaks a public interface, preserve compatibility deliberately or document the break.

Store location:
- `assets.md` / Code Iteration Principles

Status:
- pending

## Proposal 002 — Keep lightweight semantic bundles only when they clarify ownership

Pattern:
- Not every class-like or grouped structure is over-engineering, but many become vague state containers.

Suggested rule:
- Keep lightweight bundles only when they make runtime ownership clearer than passing raw loose values.

Criteria:
- The bundle groups related runtime handles.
- Field names improve readability.
- It prevents fragile tuple ordering.
- It does not hide execution flow.

Boundary:
- Do not convert a data bundle into a behavior-heavy class.
- Do not use vague names like generic context objects when concrete names would be clearer.
- Do not replace a readable function signature with a hidden bag of state.

Store location:
- `assets.md` / Code Iteration Principles

Status:
- pending

## Proposal 003 — Intake should respect route enablement

Pattern:
- A scanner that ignores pipeline toggles creates hidden work and unclear system state.

Suggested rule:
- Intake should respect route enablement.

Criteria:
- Disabled worker normally means its intake route should stop enqueueing new work.
- Scanner should not create backlog for disabled routes unless explicitly intended.
- Toggle behavior should match the operator's mental model.

Boundary:
- A dedicated backlog-building mode can be introduced deliberately.
- Some runtime services may remain always-on if their purpose is independent of route processing.
- The rule applies to intake routes, not necessarily all background services.

Store location:
- `assets.md` / Pipeline Concepts

Status:
- pending

## Proposal 004 — Keep error behavior centralized by stage when it reduces branches

Pattern:
- Per-model and per-stage error handling can sprawl into repeated local branches.

Suggested rule:
- Centralize error save/log behavior when the stage-level semantics are identical.

Criteria:
- Error marker path format is shared.
- Log format is shared.
- Failure routing is shared.
- Centralization removes repeated local code without hiding different behavior.

Boundary:
- Do not centralize errors that require different recovery semantics.
- Do not hide destructive moves or retry decisions inside a generic helper.
- Do not catch exceptions only to continue silently.

Store location:
- `assets.md` / Code Iteration Principles

Status:
- pending

## Proposal 005 — Prefer route names with operational meaning

Pattern:
- Ambiguous labels like full/light or generic context names cause repeated clarification.

Suggested rule:
- Use route and variable names that describe operational meaning directly.

Criteria:
- The name tells what happens next.
- The name matches the operator's mental model.
- The name avoids vague intensity words when the real distinction is route, policy, or output type.

Boundary:
- Do not rename stable public APIs casually.
- Do not rename only for taste.
- Rename when it reduces future misunderstanding.

Store location:
- `assets.md` / Naming Principles

Status:
- pending
