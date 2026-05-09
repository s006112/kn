# Decisions

## 2026-05-08

Decision:
- Start pos inside existing private repo instead of creating a new repository.

Reason:
- Minimize friction.
- Easier to start immediately
- Avoid premature architecture
- Prioritize real usage over architecture purity.
- Allow slow evolution through real tasks.

## 2026-05-09

Decision:
- Capture code iteration principles from real p.py refactor into proposals, not directly into stable assets.

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