---
name: minimal-python-dropin
description: 以最小正确改动对既有 Python 代码进行修复/微重构，要求完全保留现有行为、删除冗余优先、仅在明确需求下改动，默认输出可直接替换的 drop-in 代码。触发词包含：drop-in code、minimal necessary changes、preserve existing functionality、remove redundant duplicate code、no clever、boring.
---

# minimal-python-dropin

## Purpose
将一次代码修改约束为“最小正确实现”：
- 完全保留现有行为（除非需求明确要求改变）
- 优先删除而非新增
- 禁止为未来扩展预留接口
- 默认输出 drop-in code（整段可直接替换的文件或函数）

## When to use
- 修 bug（NameError、ImportError、逻辑分支错误、边界处理缺失等）
- 清理明显重复/冗余代码，且不改变行为
- 小范围重构以降低重复与复杂度，但必须可验证不改行为
- 需要“最小 diff、可回滚、可审计”的修改

## Hard constraints (non-negotiable)
- Preserve existing behavior exactly.
- If a feature is not explicitly required, DO NOT implement it.
- Prefer deletion over addition.
- Do NOT add abstractions unless strictly necessary.
- Do NOT add flexibility for future use.
- Only add code when critical.
- Deterministic behavior only.
- The result should feel boring. If the code feels clever, it is wrong.
- Smallest correct implementation.

## Inputs required
The user must provide at least one of:
- The exact file(s) content, or a repo/workspace context accessible to the agent
- The exact error trace / failing behavior
- The explicit requirement of what must change (and what must not)

If any of these are missing and cannot be inferred safely:
- Ask for confirmation with the smallest number of targeted questions.
- If still ambiguous, refuse to guess and explain what is missing.

## Output format
Default:
- DROP-IN CODE only (full file content for each changed file).
- Include a short “Changed files list” and “Behavior notes” (no long prose).

If the user explicitly asks for diff:
- Provide unified diff instead.

Never output both unless explicitly requested.

## Workflow
1. Read the relevant files and identify the smallest scope of change.
2. Identify invariants:
   - what behavior must remain identical
   - what is explicitly required to change
3. Locate the minimal edit point:
   - prefer deleting redundant code
   - avoid moving code unless necessary
4. Implement the smallest correct change.
5. Validation (minimal but real):
   - run existing tests if present
   - otherwise run the narrowest possible command(s) to prove the fix (e.g. import, minimal execution path)
   - do not introduce new test frameworks
6. Final output:
   - direct update the drop-in code
   - list assumptions (should be empty; if not, highlight them)
   - exact commands executed and results (if available)

## Refactoring rules
Allowed:
- Delete dead code / duplicate code when behavior is provably unchanged
- Rename variables only if it reduces confusion and does not change public interfaces
- Inline trivial helpers if it reduces indirection and is safer

Disallowed:
- New layers (classes, factories, registries) unless required for correctness
- Generalized frameworks, plugin systems, configuration refactors
- “Future-proofing”
- Reformat-only changes that create noise (unless needed to fix syntax)

## Ethos (decision heuristics)
- Shame in guessing APIs, Honor in careful research
- Shame in vague execution, Honor in seeking confirmation
- Shame in assuming business logic, Honor in human verification
- Shame in creating interfaces, Honor in reusing existing ones
- Shame in skipping validation, Honor in proactive testing
- Shame in breaking architecture, Honor in following specifications
- Shame in pretending to understand, Honor in honest ignorance
- Shame in blind modification, Honor in careful refactoring

## Example user prompt
Use skill: minimal-python-dropin

Goal:
- Fix: reply_body is not defined (NameError)
Constraints:
- Completely preserve existing functionality
- Minimal necessary changes only
- Remove redundant duplicate code if encountered, but only when safe

Output:
- drop-in code for ali_email.py only
