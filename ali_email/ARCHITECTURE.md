# ARCHITECTURE.md

ARCHITECTURE.md 定位是系统宪法，不是施工图。只写：
- Step 的职责
- 不能做什么
- 与其他 Step 的关系

## Purpose

This document defines the **non-negotiable architecture and invariants** of the ALI email review system.  
Its role is to **prevent complexity drift**, **block logic backflow**, and **ensure long-term system stability**.

If code behavior and this document conflict, **this document is authoritative**.

---

## Core System Invariants (MUST NOT VIOLATE)

1. **No Autonomous Action**
   - The system MUST NOT send messages to customers or third parties.
   - All generated content is INTERNAL-ONLY and reviewer-facing.

2. **Silence Means Termination**
   - An empty reviewer reply is treated as REJECT.
   - Processing MUST stop immediately after marking the message as SEEN.

3. **Any Reply Is an Override**
   - Any non-empty reviewer reply is interpreted as override instructions.
   - A regenerated INTERNAL review MUST be produced.

4. **Forward-Only Input Model**
   - Only emails explicitly forwarded by a human reviewer are accepted.
   - All outbound messages are reviewer-only.

These invariants are enforced at the orchestration layer and MUST NOT be bypassed.

---

## High-Level Pipeline
Step0 → Step1 → Step2 → Step3 → Step4 → Step5

### Step Definitions

- **Step0: Input Normalization**
  - Subject/body normalization
  - Override extraction
  - No decisions, no rejection

- **Step1: Routing**
  - Determines which routine the input follows
  - Outputs signals only (category, intent, risk)
  - MUST NOT generate content or invoke tools

- **Step2: Retrieval / Tools**
  - Decides whether and how to use RAG/tools
  - Driven strictly by routing output
  - No content generation

- **Step3: Draft Generation**
  - v1: rewrite
  - v2+: edit-only
  - MUST preserve existing behavior exactly

- **Step4: Reflection (Optional)**
  - Self-check / refinement loop
  - Gated, bounded, and explicitly enabled
  - MUST NOT alter routing or retrieval decisions

- **Step5: Packaging**
  - Review protocol rendering
  - Metadata and versioning
  - No semantic changes

---

## Module Responsibilities (Hard Boundaries)

| Module            | Responsibility                                  |
|-------------------|--------------------------------------------------|
| `ali_email/ali_email.py`    | Orchestration, sequencing, invariants enforcement |
| `ali_email/ali_fetch.py`    | IMAP fetching only                               |
| `ali_email/ali_mail_parse.py` | Parsing, normalization, review state extraction |
| `ali_email/ali_router.py`   | Routing (routine selection only)                 |
| `ali_email/ali_llm.py`      | Steps 0–3 generation logic                       |
| `ali_email/ali_send.py`     | Outbound delivery (reviewer-only)                |

**Logic MUST NOT migrate upward.**  
Orchestration MUST remain thin and stable.

---

## Routing Contract

**Routing = selecting the execution routine, not deciding content.**

- Routing outputs signals.
- Routing is deterministic and testable.
- Routing MUST NOT:
  - Call LLMs
  - Perform retrieval
  - Influence phrasing or tone directly

If routing output is mocked, the rest of the pipeline MUST still function.

---

## Evolution Rules

- `ali_email/ali_email.py` is considered **STABLE**
  - Bug fixes only
  - No new features
  - No new logic

- Feature evolution MUST occur in:
  - `ali_email/ali_router.py`
  - `ali_email/ali_llm.py`
  - Future Step4 modules

- Legacy code may exist ONLY if:
  - Fully isolated
  - Not on the active execution path
  - Clearly marked as legacy

---

## Anti-Patterns (Explicitly Forbidden)

- Adding routing logic to orchestration
- Mixing parsing with generation
- Letting LLMs decide control flow
- Adding “future-proof” hooks without immediate use
- Silent fallback behaviors
- Implicit policy encoded in prompts

---

## Change Discipline

Before any architectural change, ask:

1. Does this violate any invariant?
2. Does this introduce backward logic flow?
3. Does this move intelligence upward?
4. Can this be deleted later without behavior change?

If any answer is unclear, **do not proceed**.

---

## Final Note

This architecture is intentionally conservative.

Stability, auditability, and constraint clarity are prioritized over flexibility.
All future intelligence must live **within explicit, bounded steps**.
