# pos

Personal operating system for reusable judgment.

## Purpose

`pos` is not a note dump. It is a small operating layer for preserving judgment that improves future code review, refactor decisions, project steering, and AI-agent collaboration.

It stores:

- current working context
- accepted decisions
- pending proposals
- stable reusable judgment assets
- rules for AI agents working in this folder

## Core principle

Evolve through real work, not theory.

A thought becomes useful only when it helps future judgment. Do not turn every observation into a rule. Do not add structure because it looks organized. Add only what reduces future cognitive load or improves decision quality.

## File roles

- `context.md`: current focus, active concerns, near-term direction.
- `decisions.md`: accepted decisions with reasons and boundaries.
- `proposals.md`: candidate rules extracted from real work but not yet stable.
- `assets.md`: stable reusable patterns and rules.
- `AGENTS.md`: operating rules for AI agents editing or using this folder.

## Boundary

The system should stay small.

Good POS content:

- improves future review
- clarifies responsibility boundaries
- prevents repeated bad patterns
- captures reusable judgment from real work
- helps AI produce smaller, cleaner, more bounded changes

Bad POS content:

- records temporary implementation details
- preserves one-off opinions as rules
- creates framework language without operational value
- adds categories before repeated use proves the need
- makes future work slower to understand
