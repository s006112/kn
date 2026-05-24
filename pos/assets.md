# Assets

Stable reusable judgment patterns.

## Semantic Compression Over Defensive Completeness

Pattern:
- AI-generated code often adds local guards, fallback branches, exception blocks, and logs to appear robust.
- This can make code locally defensible but globally harder to understand.
- In personal/internal tools, excessive fallback handling often protects recoverable operator mistakes while making the normal path harder to simulate.

Rule:
- Add defensive code only when it protects a real system boundary.
- Prefer the smallest structure that preserves behavior and makes the runtime model easier to simulate.
- Prefer visible crash over guardrail bloat for recoverable manual workflow mistakes.

Criteria:
- The guard prevents data loss, duplicate execution, financial loss, irreversible action, silent corruption, or hard-to-diagnose failure.
- The fallback matches business semantics.
- The log helps future diagnosis instead of repeating obvious state.
- The branch represents a real decision, not theoretical completeness.
- The failure would be harder to diagnose if allowed to crash visibly.

Boundary:
- Do not remove protection around destructive operations, external side effects, money, file movement, or silent data corruption.
- Do not add `try/except` only because an API might fail if failure should stop the current operation.
- Do not add invalid-result branches when the default route already safely handles invalid output.
- Do not add verbose fallback code only to handle skipped intermediate CLI steps, missing trace artifacts, or other recoverable personal workflow mistakes.
- Keep hard guards around real file mutation, scope boundaries, destructive actions, irreversible actions, silent corruption, money, or external side effects.

Success criteria:
- The code is easier to explain.
- The failure behavior is intentional.
- The normal path stays visible.
- The fallback is business-semantic, not generic panic handling.
- Personal workflow mistakes fail loudly enough to fix without adding long defensive branches.

## Extract Helpers Only From Real Reuse

Pattern:
- AI tends to over-abstract small local duplication into helpers.

Rule:
- Do not introduce a helper unless it reduces total cognitive complexity.

Criteria:
- The helper is used by real repeated call sites.
- The helper names a meaningful concept.
- The helper reduces future edit risk.
- The helper hides mechanical detail, not business decisions.

Boundary:
- Do not add a helper only to hide two or three local lines.
- Do not add a helper if readers must jump around more to understand the flow.
- Do not extract workflow-specific logic into a vague shared helper file.

Success criteria:
- The top-level flow is easier to read after extraction.
- The helper has low-context inputs.
- The helper does not require many parameters to recreate hidden context.

## Optimize For Cognitive Compression, Not Raw Line Count

Pattern:
- Line-count reduction can create semantic loss.
- Line-count increase can also hide structural bloat behind formal completeness.

Rule:
- Optimize for fewer concepts, clearer ownership, and easier mental simulation.

Criteria:
- Runtime ownership becomes clearer.
- Execution order becomes easier to follow.
- Behavior remains equivalent unless a change is explicitly accepted.
- The change removes real concepts, branches, or indirection, not just whitespace.

Boundary:
- Do not inline meaningful concepts merely to reduce files or lines.
- Do not keep abstractions that no longer carry useful meaning.
- Do not accept code growth unless it buys a clearer boundary or safer behavior.

Success criteria:
- The code becomes shorter or conceptually smaller.
- Future patch risk decreases.
- A reviewer can explain the flow with fewer moving parts.

## No Custom OOP Unless External Interface Requires It

Pattern:
- Personal code should default to explicit functions, small modules, and plain data structures.
- Custom classes often hide state and invite Manager/Service/Controller-style inflation.

Rule:
- Do not add custom object-oriented classes unless an external library or framework requires a class, subclass, handler, or callback object.

Criteria:
- A class is allowed only when external code requires a class-shaped interface.
- The class must remain a thin adapter.
- Core business logic should stay in explicit functions whenever practical.

Boundary:
- Do not add classes for architecture neatness, grouping functions, lifecycle wrapping, state containers, managers, services, controllers, orchestrators, registries, or future extensibility.
- Do not replace a class with a giant implicit object hidden in a dict.
- If shared runtime state is needed, prefer plain concrete names and visible function inputs/outputs.

Success criteria:
- Reading the top-level flow does not require following `self.*` chains.
- AI patching cannot introduce abstraction layers without a hard reason.
- Runtime ownership remains explicit and locally reviewable.

## Treat Public Function Signatures As Contracts

Pattern:
- AI refactor often changes interface shape accidentally.

Rule:
- Before changing a public or test-facing function signature, identify likely callers and compatibility impact.

Criteria:
- Keep the old entry point if compatibility matters.
- Make intentional breaks explicit.
- Update evaluation code when behavior or interface shape legitimately changes.

Boundary:
- Internal-only functions can change more freely.
- Public, test-facing, or cross-module functions require stricter review.

Success criteria:
- Evaluation failures reflect real behavior changes, not accidental interface drift.
- Callers remain aligned.
- Compatibility shims are deliberate and temporary, not accidental clutter.

## Build Extension Foundation Before Extension Features

Pattern:
- Adding a useful feature before the foundation is clean often creates another special case.

Rule:
- Fix extension surfaces before adding extension features.

Criteria:
- The new feature requires a route, queue, processor, model group, folder, or runtime thread.
- Existing ownership boundaries are unclear.
- The feature would copy a special case instead of following a clean template.

Boundary:
- Do not block small bug fixes.
- Do not use foundation cleanup as an excuse for unlimited refactor.
- Feature pause is justified only when the feature exposes real structural debt.

Success criteria:
- New routes can be added by following an existing pattern.
- Route, queue, worker, processor, and model policy responsibilities are visible.
- The next feature does not require another one-off path.

## Separate Pipeline Concepts Before Optimizing Code Shape

Pattern:
- Runtime services, file routes, workers, processors, and model policies are often mixed into one layer.

Rule:
- Separate concepts before deciding whether to split files, extract helpers, or rewrite flow.

Criteria:
- Runtime service: long-lived loop, watcher, or scheduler.
- File route: intake path from folder or file type to queue.
- Worker: queue consumer.
- Processor: single-job executor.
- Model policy: model list, routing behavior, merge behavior, and distillation behavior.

Boundary:
- Do not create heavy framework abstractions.
- Do not split files only for visual neatness.
- Conceptual separation can exist before file separation.

Success criteria:
- Each component can be named by responsibility.
- Toggles and queues match the operator's mental model.
- Future route expansion has a clear insertion point.

## Name Components By Responsibility

Pattern:
- Misnamed components cause repeated design confusion even when the code works.

Rule:
- Rename components when the current name describes mechanism instead of responsibility.

Criteria:
- The name forces repeated explanation.
- The component is mistaken for another architectural layer.
- The name hides ownership, boundary, or lifecycle.
- A better name reduces future patch risk.

Boundary:
- Do not rename stable public APIs casually.
- Do not rename only for style preference.
- Rename only when semantic clarity improves future maintenance.

Success criteria:
- The name tells the reader what the component owns.
- Scheduling mechanism does not masquerade as business purpose.
- Future patches are less likely to attach logic to the wrong layer.

## Repo Polish Agent Pattern

Pattern:
- Use a bounded judgment loop before allowing repo-changing automation.

Loop:
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
- Touched files are explicit.
- Behavior-preservation boundary is clear.
- Risks and stop conditions are stated.
- Verification commands are included.
- The accepted plan is clean enough to guide human execution or a future patch agent.




# Rule: Complex Problem Methodology

When facing a complex problem, do not jump to solution.

First define:
1. What system/object is this?
2. What state needs to be changed?
3. What is the success criterion?
4. What variables affect the result?
5. What constraints cannot be violated?
6. What real-world scenarios may occur?
7. What are the highest-risk failure points?
8. What should be done first based on risk, cost, and expected return?
9. How will the result be verified?
10. What reusable rule/template/checklist can be extracted after completion?


Archive 不應由人類即時主觀判斷主導，而應由系統根據任務復用、結果反饋、遷移能力、失效記錄與長期價值自動學習保存策略。人類保留 objective、constraint、evaluation 和 final approval，而不是手工控制每個分類與取捨。