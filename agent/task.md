# Task

Evaluate where `is_file_ready()` should live.

## Question

Should TTML file readiness / file size stability checking stay in `w/p_ttml.py`,
move into `w/p_pipelines.py`, or become a generic helper?

## Decision criteria

- Distinguish file readiness from TTML format conversion.
- Distinguish readiness checking from queue scanning and file locking.
- Prefer local ownership if only one real caller exists.
- Do not extract a shared helper unless at least two real call sites need it.
- Preserve current behavior.
- Produce a plan only. Do not edit files.

## Allowed files

- w/p_pipelines.py
- w/p_ttml.py
- w/p_audio.py
- w/evaluation.py

## Success criteria

- Clear ownership decision for `is_file_ready()`
- Explicit boundary between pipeline orchestration and TTML conversion
- No premature helper extraction
- Minimal patch plan only