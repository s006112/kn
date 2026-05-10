# Assets

## Repo Polish Agent Pattern

Pattern:
- Plan: generate a minimal patch plan from task context, long-term rules, and allowed files.
- Review: critique the plan against task constraints, boundaries, and risks.
- Revise: produce a clean standalone plan that can be accepted or rejected.
- Accept: human approval promotes the chosen plan into an accepted execution artifact.

Boundary:
- Runtime trace is evidence, not a stable asset.
- Accepted plans are execution artifacts, not automatically permanent rules.
- Long-term assets should contain reusable patterns, not temporary run outputs.
- Model output must remain separate from human acceptance.

Rules:
- Keep planning separate from execution.
- Keep review separate from revision.
- Keep model output separate from human acceptance.
- Do not automate repo edits before the plan is accepted.
- Do not expand agent capability faster than the quality of its judgment loop.

Success criteria:
- The plan is minimal.
- The touched files are explicit.
- The behavior-preservation boundary is clear.
- Risks and stop conditions are stated.
- Verification commands are included.
- The accepted plan is clean enough to guide human execution or a future patch agent.

## No Custom OOP Unless External Interface Requires It

Pattern:
- Personal code should default to explicit functions, small modules, and plain data structures.
- Custom object-oriented classes are forbidden unless an external library or framework interface requires a class, subclass, handler, or callback object.

Criteria:
- Allowed only when external code requires a class-shaped interface.
- The class must stay as a thin adapter around the required interface.
- Core business logic should remain in explicit functions outside the class whenever practical.

Boundary:
- Do not add classes for architecture neatness, grouping functions, lifecycle wrapping, state containers, managers, services, controllers, orchestrators, registries, or future extensibility.
- Do not replace a class with a giant implicit object hidden in a dict.
- If shared runtime state is needed, prefer explicit `ctx`, simple data structures, and visible function inputs/outputs.

Rules:
- No custom OOP by default.
- External interface requirement is the only accepted exception.
- Keep execution order, state ownership, and side effects visible.
- If a class is unavoidable, isolate it at the boundary and keep the real logic function-first.

Success criteria:
- Reading the top-level flow does not require jumping through `self.*` chains.
- AI patching cannot introduce Manager/Service/Controller-style abstraction layers.
- Runtime ownership remains explicit and locally reviewable.
