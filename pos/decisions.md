# Decisions

Accepted decisions with reasons and boundaries.

## 2026-05-08 — Keep `pos` inside the existing private repo

Decision:
- Start `pos` inside the existing private repo instead of creating a new repository.

Reason:
- Minimize friction.
- Start immediately.
- Avoid premature architecture.
- Prioritize real usage over structural purity.
- Let the system evolve slowly through real tasks.

Boundary:
- Do not split `pos` into a separate repo until repeated usage proves the benefit.

## 2026-05-09 — Capture code-iteration principles as proposals first

Decision:
- Capture code-iteration principles from real refactor work into proposals before promoting them into stable assets.

Reason:
- The principles came from real code review and cleanup.
- They are useful but need repeated validation.
- Stable assets should not expand too quickly.

Boundary:
- Repeated validated patterns may be promoted later with human approval.

## 2026-05-09 — Do not extract agent-specific helpers prematurely

Decision:
- Keep agent workflow helpers inside `agent/agent.py` while the agent is still small and read-only.
- Extract only when repeated usage appears across at least two real call sites.
- Generic file IO helpers may belong in shared helper modules.
- Agent-specific parsing, POS loading, and prompt assembly should stay near the agent workflow.

Reason:
- Avoid vague helper collections.
- Preserve local readability of the running flow.
- Prevent helper sprawl caused by visual tidiness instead of real reuse.
- Keep shared helpers limited to stable, generic, low-context operations.

Boundary:
- Good helper extraction:
  - generic text read/write
  - optional file read
  - safe filename or path handling
  - repeated low-context utilities used by multiple modules
- Bad helper extraction:
  - single-caller parsing helpers
  - POS-loading helpers with no second caller
  - prompt assembly helpers that hide workflow decisions
  - helpers requiring many parameters to recreate local context

Rule:
- Extract from real repetition, not imagined future reuse.

## 2026-05-09 — Establish the repo polish agent loop

Decision:
- Establish the repo polish agent loop as Plan → Review → Revise → Accept.
- Keep the agent in planning and judgment mode before adding execution automation.
- Treat `agent/final_plan.md` as the human-accepted execution artifact.
- Treat trace files as runtime evidence, not stable assets.

Reason:
- The goal is not a flashy autonomous coding agent.
- The goal is structured, reviewable, reusable repo polishing.
- Planning quality must stabilize before patch execution is automated.
- Human approval remains the gate between model output and repo-changing action.

Boundary:
- Agent may:
  - read task context
  - read POS context
  - read allowed repo files
  - generate a minimal patch plan
  - review the plan
  - revise the plan
  - save an accepted final plan after human approval
- Agent must not:
  - edit repo files automatically
  - apply patches automatically
  - promote runtime trace into POS assets automatically
  - expand scope beyond allowed files
  - treat model output as accepted without human approval

Rule:
- Do not add execution automation until the planning loop consistently produces clean, bounded, verifiable plans.

## 2026-05-10 — Pause GOSSIP extraction shortcut until foundation is clean

Decision:
- Pause the planned GOSSIP extraction shortcut.
- Clean the pipeline foundation first.
- Treat route expansion as blocked until intake, queue, worker, and processor boundaries are clearer.

Reason:
- The shortcut is directionally useful but would currently increase structural debt.
- The runtime mixes scanner service, file intake route, processing worker, queue ownership, and model policy.
- Adding a route now would create another special case instead of a reusable extension pattern.

Boundary:
- Do not add GOSSIP routing yet.
- Do not change pretext, extract, audio, or ttml processing internals as part of foundation cleanup.
- First clarify orchestration, scanner semantics, route enablement, and naming.
- Feature routes may resume after the foundation can absorb them cleanly.

Rule:
- Build the extension foundation before adding extension features.

## 2026-05-10 — Treat `PeriodicScanner` as file intake, not a business pipeline

Decision:
- Treat `PeriodicScanner` as a misnamed runtime service, not a business pipeline.
- Reframe it as file intake and queue routing.

Reason:
- It supplies multiple pipelines instead of processing one business route.
- Disabling workers while scanner still enqueues files creates semantic mismatch.
- The name describes timing behavior, not responsibility.
- Future routes need a clear intake location.

Boundary:
- Rename conceptually toward file intake responsibility.
- Scanner should only discover files and enqueue enabled routes.
- Workers should only consume queues.
- Processors should only process jobs.
- Do not make the scanner a processor.

Rule:
- Name runtime components by responsibility, not by scheduling mechanism.

## 2026-05-10 — Forbid custom OOP by default

Decision:
- Establish a strict no-custom-OOP rule for the personal codebase.
- The only accepted exception is when an external library or framework requires a class, subclass, handler, or callback object.

Reason:
- Keep execution flow explicit.
- Prevent AI-generated Manager/Service/Controller abstraction layers.
- Avoid hidden state behind `self.*`.
- Improve local readability, patch review, and long-term maintainability.

Boundary:
- Do not use custom classes for grouping functions, architecture neatness, lifecycle wrappers, state containers, orchestrators, registries, or future extensibility.
- If a class is forced by an external interface, keep it thin and push core logic back into functions.

Rule:
- No custom OOP unless required by an external interface.

## 2026-05-12 — Treat defensive code as a semantic boundary decision

Decision:
- Do not let AI add defensive branches, fallback paths, or exception handling by default.
- Require each defensive structure to justify the real boundary it protects.

Reason:
- AI coding often creates local formal completeness while increasing global complexity.
- Many guards, logs, and invalid-state branches do not protect real data or behavior.
- The goal is code that is easier to mentally simulate, not code that appears safe in isolation.

Boundary:
- Keep defensive code when it protects data integrity, irreversible actions, external side effects, financial loss, duplicate execution, or silent corruption.
- Remove or reject defensive code when the default route already handles the case safely.
- A fallback should express business semantics, not generic fear.

Rule:
- Defensive code must earn its place by protecting a real system boundary.
