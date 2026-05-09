# Proposals

## Proposal 001

Pattern:
- AI tends to over-abstract small local duplication.

Suggested Rule:
- Do not introduce new helper functions unless they reduce overall cognitive complexity.

Criteria:
- Helper reduces repeated logic across real call sites.
- Helper gives a better name to a meaningful concept.
- Helper reduces future edit risk.

Boundary:
- Do not add helper only to reduce 2-3 local lines.
- Do not add helper if reader must jump around more to understand the flow.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending

## Proposal 002

Pattern:
- Hidden global state makes runtime ownership unclear.

Suggested Rule:
- Prefer explicit dependency passing over module-level mutable state.

Criteria:
- A function can receive the needed object directly.
- Runtime handle already exists.
- Global state only exists for convenience.

Boundary:
- Temporary global state is acceptable only for true singleton process interfaces or compatibility shims.
- If removing global state breaks a public interface, preserve compatibility deliberately or document the interface break.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending

## Proposal 003

Pattern:
- Not every class-like structure is over-engineering.

Suggested Rule:
- Keep lightweight semantic bundles when they clarify runtime ownership.

Criteria:
- The object groups related runtime handles.
- Field names improve readability.
- It prevents fragile tuple ordering.

Boundary:
- Do not convert a data bundle into a behavior-heavy class without need.
- Do not replace a named bundle with raw tuple/dict if that weakens meaning.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending

## Proposal 004

Pattern:
- Guardrails and convenience defaults can become noise when deployment assumptions are stable.

Suggested Rule:
- Remove defensive setup code when failure is non-fatal, externally guaranteed, and the code no longer earns its cognitive cost.

Criteria:
- Missing condition is already guaranteed by environment, deployment, or manual setup.
- Failure would be obvious and easy to diagnose.
- The removed code does not protect data integrity or irreversible actions.

Boundary:
- Do not remove guardrails protecting data loss, duplicate execution, financial loss, or silent corruption.
- Do not remove checks only because they look boring.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending

## Proposal 005

Pattern:
- Line-count reduction can create semantic loss.

Suggested Rule:
- Optimize for cognitive compression, not raw brevity.

Criteria:
- Code becomes easier to explain.
- Runtime ownership becomes clearer.
- Fewer concepts are needed to understand the flow.
- Behavior remains equivalent unless the change is explicitly accepted.

Boundary:
- Do not inline meaningful concepts merely to reduce files or lines.
- Do not keep abstractions that no longer carry useful meaning.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending

## Proposal 006

Pattern:
- AI refactor often changes interface shape accidentally.

Suggested Rule:
- Treat public function signatures as contracts.

Criteria:
- Before changing a signature, identify all likely callers.
- If compatibility is not needed, make the break explicit.
- If compatibility is needed, preserve old entry point or provide a shim.

Boundary:
- Internal-only functions can change more freely.
- Public or test-facing functions require stricter review.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending