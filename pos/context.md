# Context

Current focus:
- Building a lightweight personal operating system (`pos`).
- Exploring AI-assisted judgment asset accumulation.
- Keeping the system minimal, low-friction, and driven by real tasks.
- Cleaning project foundations before adding new feature routes.
- Treating live code work as evidence for reusable code-iteration judgment.

Current concerns:
- Avoid over-engineering.
- Avoid rule explosion.
- Prefer small reusable patterns.
- Avoid AI-generated helper/function/class inflation.
- Preserve explicit runtime boundaries.
- Keep code iteration focused on reducing cognitive load, not only reducing line count.
- Do not add feature routes before the underlying extension surface can absorb them cleanly.
- Separate runtime services, file routes, queues, processors, and model policies.

Active project direction:
- Pause non-essential feature expansion.
- First clean the pipeline foundation.
- Clarify the boundary between runtime services and business pipelines.
- Reframe scanner-like components as file intake and queue routing services.
- Make future route expansion predictable before adding more routes.

Current pipeline cleanup target:
- Scanner services only discover files and enqueue enabled routes.
- Workers only consume their own queues.
- Processors only process one job type.
- Config separates route, folder, model list, and processing policy.

Near-term priority:
1. Rename and clarify scanner-service semantics.
2. Make intake scanning respect enabled route toggles.
3. Split intake logic into clear route-level sections.
4. Clean route / folder / model naming.
5. Revisit feature routing only after the foundation is clean.