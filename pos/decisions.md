# Decisions

## 2026-05-08

Decision:
- Start `pos` inside the existing private repo instead of creating a new repository.

Reason:
- Minimize friction.
- Easier to start immediately.
- Avoid premature architecture.
- Prioritize real usage over architecture purity.
- Allow slow evolution through real tasks.

## 2026-05-09

Decision:
- Capture code iteration principles from real `p.py` refactor into proposals, not directly into stable assets.

Reason:
- The principles came from actual code review and cleanup.
- They are useful but still need repeated validation across more code work.
- Avoid prematurely expanding approved rules.
- Keep assets stable and proposals experimental.

## 2026-05-09

Decision:
- Do not extract small agent-specific helpers prematurely into a shared helper file.
- Keep agent workflow helpers inside `agent/agent.py` while the agent is still small and read-only.
- Only extract helpers when real repeated usage appears across at least two call sites.
- Generic file IO helpers may belong in `helper/helper_files.py`, but agent-specific parsing, POS loading, and prompt assembly should stay near the agent workflow.

Reason:
- Avoid creating a vague helper collection file.
- Preserve local readability of the agent running flow.
- Prevent helper sprawl caused by visual tidiness rather than real reuse.
- Keep shared helpers limited to stable, generic, low-context operations.
- Extracting too early increases navigation cost and hides important workflow logic.

Boundary:
- Good helper extraction:
  - generic text file read/write
  - optional file read
  - safe filename/path handling
  - repeated low-context utilities used by multiple modules
- Bad helper extraction:
  - `parse_allowed_files()` before another real caller exists
  - `load_pos_context()` because it is agent/POS-specific
  - `build_prompt()` because it is prompt assembly logic
  - any helper that requires many parameters or hides workflow decisions

Rule:
- Extract from real repetition, not imagined future reuse.

## 2026-05-09

Decision:
- Establish the repo polish agent loop as:
  - Plan
  - Review
  - Revise
  - Accept
- Keep the agent in planning and judgment mode before adding execution automation.
- Treat `agent/final_plan.md` as the human-accepted execution artifact.
- Treat `agent/last_prompt.md`, `agent/last_plan.md`, `agent/last_review.md`, and `agent/last_revised_plan.md` as runtime trace files, not stable assets.

Reason:
- The goal is not to build a flashy autonomous coding agent.
- The goal is to make repo polishing more structured, reviewable, and reusable.
- Planning quality must stabilize before patch execution is automated.
- Human approval remains the gate between model output and repo-changing action.
- A clean accepted plan is a better next-step boundary than raw LLM output.

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

## 2026-05-10

Decision:
- Pause the planned GOSSIP extraction shortcut.
- Clean the pipeline foundation first.
- Treat route expansion as blocked until the intake / queue / worker / processor boundaries are clearer.

Reason:
- The GOSSIP shortcut is directionally useful, but adding it now would increase structural debt.
- The current runtime mixes scanner service, file intake routes, processing workers, queue ownership, and model policy.
- Adding a new route before cleaning the foundation would create another special case instead of a reusable extension pattern.
- Long-term extensibility is more valuable than one more working feature.

Boundary:
- Do not add GOSSIP routing yet.
- Do not change pretext/extract/audio/ttml processing internals as part of the foundation cleanup.
- First clarify orchestration, scanner semantics, route enablement, and naming.
- Feature routes may resume after the foundation can absorb them cleanly.

Rule:
- Build the extension foundation before adding extension features.

## 2026-05-10

Decision:
- Treat `PeriodicScanner` as a misnamed runtime service, not a business pipeline.
- Reframe it as file intake and queue routing.

Reason:
- It is a thread, but it supplies multiple pipelines instead of processing one business route.
- Disabling processing workers while the scanner still enqueues files creates semantic mismatch.
- The name describes timing behavior, not responsibility.
- Future routes such as GOSSIP need a clear intake location.

Boundary:
- Rename conceptually toward `FileIntakeScanner`.
- Scanner should only discover files and enqueue enabled routes.
- Workers should only consume queues.
- Processors should only process jobs.
- Do not make the scanner a processor.

Rule:
- Name runtime components by responsibility, not by scheduling mechanism.

## 2026-05-10

Decision:
- Establish a strict no-custom-OOP rule for the personal codebase.
- The only accepted exception is when an external library or framework requires a class, subclass, handler, or callback object.

Reason:
- Keep execution flow explicit.
- Prevent AI-generated Manager/Service/Controller-style abstraction layers.
- Avoid hidden state behind `self.*`.
- Improve local readability, patch review, and long-term maintainability.

Boundary:
- Do not use custom classes for grouping functions, architecture neatness, resource lifecycle wrappers, state containers, orchestrators, registries, or future extensibility.
- If a class is forced by an external interface, keep it thin and push core logic back into functions.

Rule:
- No custom OOP unless required by an external interface.
