# pos/AGENTS.md

This folder stores reusable judgment assets, not ordinary notes.

## Operating rules

- Preserve the boundary between context, decisions, proposals, and stable assets.
- Do not promote proposals into assets without explicit human approval.
- Do not convert temporary implementation details into permanent rules.
- Do not expand POS structure unless repeated usage proves the need.
- Prefer small durable judgment patterns over large theoretical frameworks.
- When uncertain, store the item as a proposal, not an asset.

## Asset quality

A stable asset must help future code review, refactor, project steering, or AI-agent behavior.

Each asset should normally include:

- Pattern
- Rule
- Criteria
- Boundary
- Success criteria

Keep entries short, scoped, and reusable.

## Code-iteration capture

Extract rules only from real code work.

Do not preserve every frustration as a rule. Capture only the part that is likely to recur and likely to improve future decisions.

Separate:

- runtime trace from accepted artifact
- accepted decision from reusable rule
- local implementation fact from general pattern
- proposal from stable asset

## AI-agent discipline

AI must optimize for semantic compression, not defensive verbosity.

Before adding code, helpers, wrappers, classes, branches, validations, or error handling, identify the real responsibility being protected.

Reject changes that add:

- local formal completeness without system value
- defensive branches that do not protect data, money, irreversible action, or silent corruption
- helper functions that reduce line count but increase navigation cost
- abstraction names that hide concrete runtime ownership
- framework-shaped structure before repeated use proves the need

Default to the smallest behavior-preserving change that makes the runtime model easier to simulate.
