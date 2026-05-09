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