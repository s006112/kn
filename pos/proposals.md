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

## Proposal 007

Pattern:
- Adding a useful feature before the foundation is clean can increase long-term complexity.

Suggested Rule:
- Fix extension surfaces before adding extension features.

Criteria:
- New feature requires a new route, queue, processor, model group, folder, or runtime thread.
- Existing architecture has unclear ownership boundaries.
- The new feature would copy an existing special case instead of following a clean template.

Boundary:
- Do not block small bug fixes.
- Do not use foundation cleanup as an excuse for unlimited refactor.
- Feature pause is justified only when the feature exposes real structural debt.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending

## Proposal 008

Pattern:
- Runtime services, file routes, workers, processors, and model policies are often mixed into one layer.

Suggested Rule:
- Separate pipeline system concepts before optimizing code shape.

Criteria:
- Runtime service: long-lived loop or scheduler.
- File route: intake path from folder/file type to queue.
- Worker: queue consumer.
- Processor: single-job executor.
- Model policy: model list and distillation/merge behavior.

Boundary:
- Do not create heavy framework abstractions.
- Do not split files only for visual neatness.
- Separation can be conceptual first, then reflected in code when useful.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending

## Proposal 009

Pattern:
- Misnamed components cause repeated design confusion even when the code works.

Suggested Rule:
- Rename components when the current name describes mechanism instead of responsibility.

Criteria:
- The name forces repeated explanation.
- The component is mistaken for another architectural layer.
- The name hides ownership, boundary, or lifecycle.
- A better name reduces future patch risk.

Boundary:
- Do not rename stable public APIs casually.
- Do not rename only for style preference.
- Rename when semantic clarity improves future maintenance.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending

## Proposal 010

Pattern:
- A scanner that ignores pipeline toggles creates hidden work and unclear system state.

Suggested Rule:
- Intake should respect route enablement.

Criteria:
- Disabled worker means its intake route should normally stop enqueueing new work.
- Scanner should not keep creating backlog for disabled routes unless explicitly intended.
- Toggle behavior should match the operator's mental model.

Boundary:
- A dedicated backlog-building mode can be introduced deliberately.
- Some runtime services may remain always-on if their purpose is independent of route processing.
- The rule applies to intake routes, not necessarily all background services.

Store Location:
- assets.md / Code Iteration Principles

Status:
- pending
