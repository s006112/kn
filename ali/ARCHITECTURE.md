# ARCHITECTURE.md

ARCHITECTURE.md 定位是系统宪法，不是施工图。只写：
- Step 的职责
- 不能做什么
- 与其他 Step 的关系

## Purpose

This document defines the active architecture and non-negotiable invariants of the ALI email review system as implemented in the current `ali/*.py` code.

Its purpose is to:
- prevent complexity drift
- keep control flow explicit
- stop logic from migrating upward into orchestration
- preserve reviewer-only safety boundaries

If code and this document diverge, this document is the intended target state. Code should be moved back toward it, not the reverse by default.

---

## Core System Invariants (MUST NOT VIOLATE)

1. **No Autonomous External Action**
   - The system MUST NOT send messages to customers or third parties.
   - All generated content is INTERNAL-ONLY and reviewer-facing.

2. **Forward-Only Reply Model**
   - The only allowed outbound recipient is the human reviewer who forwarded the original email.
   - Recipient enforcement is hard-blocked in `ali/ali_send.py`.

3. **Silence Means Termination**
   - An empty reviewer reply is treated as REJECT.
   - Processing stops after the reply is marked as SEEN.

4. **Any Non-Empty Reply Is an Override**
   - A non-empty reviewer reply is interpreted as override/edit instructions.
   - The next review version MUST be regenerated from the previous draft plus override instructions.

5. **Reserved Review Namespace**
   - Subjects containing `[ALI:vN]` are reserved for ALI review threads only.
   - New-message processing and reviewer-reply processing are separated using this namespace.

6. **Human-Only Entry**
   - Only allowlisted internal senders may enter the pipeline.
   - Non-allowlisted senders are rejected in the fetch layer and moved out of the active mailbox path.

These invariants are enforced across orchestration, fetch, and send layers and MUST NOT be bypassed by prompts or model behavior.

---

## Active Runtime Shape

The active runtime is a **two-phase polling loop**:

`pipeline_run()` in `ali/ali_email.py`

1. **Phase 1: New forwarded emails**
   - fetch unread non-review-thread messages
   - generate version 1 internal review
   - send back to reviewer only
   - mark original IMAP message as SEEN only after successful handling

2. **Phase 2: Reviewer replies on existing ALI threads**
   - fetch unread messages whose subject matches `[ALI:v`
   - if reply body is empty: treat as REJECT and mark SEEN
   - else parse last review state, extract override instructions, generate next version
   - send revised internal review to the same reviewer only
   - mark reply message as SEEN only after successful handling

Polling cadence is time-based in `ali/ali_email.py` and is not part of the semantic architecture.

---

## High-Level Pipeline

Step0 -> Step1 -> Step2 -> Step3 -> Step4 -> Step5

### Step0: Input Normalization and Review-State Parsing

Owned by:
- `ali/ali_mail_parse.py`

Responsibilities:
- normalize subject/body input
- trim and bound body size
- extract override instructions from reviewer replies
- parse prior ALI review version and draft from quoted review-thread emails

Must not:
- decide routing
- call LLMs
- perform retrieval
- send mail

Relationship:
- feeds normalized input into Step1/Step3
- feeds prior review state into v2+ edit-only generation

### Step1: Routing

Owned by:
- `ali/ali_router.py`

Responsibilities:
- classify the email into a route category
- emit route signals only:
  - `category`
  - `intent`
  - `risk_level`
  - `rationale`
  - `confidence`

Current implemented categories:
- `safety_regulation`
- `technical`
- `commercial`
- `casual`
- `unknown`

Must not:
- generate content
- call LLMs
- perform retrieval
- decide delivery behavior

Relationship:
- Step1 output gates Step2 behavior
- downstream modules may consume routing output, but must not mutate it

### Step2: Retrieval / Tools

Owned by:
- `ali/ali_llm.py` for retrieval gating
- `rag/helper_rag_pipeline.py` for retrieval execution

Responsibilities:
- decide whether retrieval is allowed for the current route
- execute RAG only when allowed

Current implementation:
- retrieval is enabled only when `route.category == "safety_regulation"`
- retrieval uses `get_rag_engine("standard")`
- retrieval failure degrades to no-context generation rather than stopping the pipeline

Must not:
- change routing output
- send mail
- own packaging

Relationship:
- Step2 is consumed by Step3 v1 generation only
- Step2 is intentionally bypassed on v2+ edit-only path

### Step3: Draft Generation

Owned by:
- `ali/ali_llm.py`

Responsibilities:
- generate the internal reviewer-facing draft

Current implemented modes:
- **v1 rewrite path**
  - normalize input
  - route
  - optionally retrieve
  - if retrieval returns an answer, use that answer as the draft
  - otherwise call the system-prompt LLM path

- **v2+ edit-only path**
  - requires `previous_draft`
  - loads `prompt_edit_only_override.txt`
  - extracts override instructions from the reviewer reply
  - edits the previous draft
  - MUST NOT rerun routing or retrieval
  - MUST NOT fall back to rewrite semantics

Must not:
- decide recipients
- mark IMAP state
- embed mailbox protocol semantics

Relationship:
- Step3 consumes Step0 and optionally Step2
- Step3 produces a draft for Step4/Step5

### Step4: Reflection

Owned by:
- `ali/ali_llm.py`

Responsibilities:
- post-generation refinement hook only

Current implementation:
- present as `step4_reflect()`
- disabled by default
- currently a no-op shell that returns the draft unchanged

Must not:
- reroute
- retrieve
- call LLMs in the current implementation
- introduce new control-flow decisions

Relationship:
- Step4 sits strictly after Step3
- it may refine a draft in future, but it must not alter Step1/Step2 outcomes

### Step5: Packaging

Owned by:
- `ali/ali_llm.py` for protocol rendering
- `ali/ali_email.py` for review subject/version sequencing

Responsibilities:
- wrap the draft with ALI review protocol markers
- assign review version
- build review-thread subject using `[ALI:vN]`

Current implementation:
- `render_review()` adds the version header/footer block
- `ali_email.py` builds the review-thread subject and sends it

Must not:
- change draft semantics
- alter routing or retrieval decisions

Relationship:
- Step5 is the final stage before outbound delivery

---

## Orchestration Contract

Owned by:
- `ali/ali_email.py`

Responsibilities:
- run the two-phase polling loop
- keep exceptions contained via guarded execution
- call downstream modules in order
- enforce subject-versioning and message lifecycle sequencing
- mark IMAP messages as SEEN only after successful processing or explicit reject handling

Must not:
- contain routing heuristics
- parse quoted review history
- implement retrieval logic
- construct prompts
- make content decisions

`ali/ali_email.py` is intentionally narrow and should remain stable.

---

## Module Responsibilities (Hard Boundaries)

| Module | Responsibility |
| --- | --- |
| `ali/ali_email.py` | Orchestration, phase sequencing, invariant enforcement, guarded execution |
| `ali/ali_fetch.py` | IMAP fetch selection, sender allowlist gate, raw-record to `EmailMessage` conversion |
| `ali/ali_mail_parse.py` | Input normalization, override extraction, review-state parsing, protocol constants |
| `ali/ali_router.py` | Deterministic route selection only |
| `ali/ali_llm.py` | Retrieval gating, v1 generation, v2+ edit-only generation, reflection hook, review rendering |
| `rag/helper_rag_pipeline.py` | Retrieval engine execution over FAISS artifacts plus answer/context assembly |
| `ali/ali_send.py` | Reviewer-only outbound delivery with forward-sender enforcement |

Logic MUST NOT migrate upward.
The further “up” a module is, the less semantic intelligence it may contain.

---

## Fetch Contract

Owned by:
- `ali/ali_fetch.py`

Rules for Phase 1:
- fetch unread messages only
- skip reserved ALI review-thread subjects
- allow only internal allowlisted senders

Rules for Phase 2:
- fetch unread messages only
- subject must match the reserved ALI review-thread namespace
- allow only internal allowlisted senders

Fetch layer may:
- define mailbox search criteria
- reject disallowed senders
- move disallowed mail away from the active path

Fetch layer must not:
- mark success consumption state for normal processing
- generate content
- parse review protocol semantics

---

## Routing Contract

Routing means selecting the execution routine, not deciding the answer.

Routing output is:
- deterministic
- side-effect free
- testable in isolation

Routing must not:
- call LLMs
- perform retrieval
- alter phrasing or tone directly
- encode reviewer-delivery policy

If routing output is mocked, the rest of the pipeline must still function.

---

## Delivery Contract

Owned by:
- `ali/ali_send.py`

Delivery rules:
- outbound email is reviewer-only
- `To:` must equal the original forwarding reviewer address
- customer addresses are never valid outbound targets
- writing to IMAP Sent is best-effort and not part of semantic decision making

Delivery must not:
- decide draft content
- decide whether a message is a reject or override
- mark source messages as SEEN

---

## Evolution Rules

- `ali/ali_email.py` is **STABLE**
  - bug fixes only
  - invariant enforcement only
  - no new semantic features unless they are strictly orchestration concerns

- Feature evolution should occur in:
  - `ali/ali_router.py`
  - `ali/ali_llm.py`
  - `rag/helper_rag_pipeline.py`
  - future dedicated Step4 modules, if Step4 becomes real

- `ali/ali_mail_parse.py` may evolve only for:
  - normalization correctness
  - override extraction correctness
  - review protocol parsing correctness

- Legacy or experiment code may exist only if:
  - isolated from active execution
  - not referenced by `ali/ali_email.py`
  - clearly marked as non-authoritative

---

## Anti-Patterns (Explicitly Forbidden)

- adding routing logic to `ali/ali_email.py`
- mixing parsing with generation
- letting LLMs decide control flow
- rerunning retrieval on the v2+ edit-only path
- weakening the reviewer-only send constraint
- bypassing the reserved `[ALI:vN]` review namespace
- silently changing Step4 from no-op to semantic controller
- hiding policy in prompts when it belongs in code

---

## Change Discipline

Before making an architectural change, ask:

1. Does this violate any invariant?
2. Does this move semantic intelligence upward?
3. Does this blur the Phase1 vs Phase2 split?
4. Does this let model output influence control flow?
5. Does this weaken reviewer-only safety enforcement?

If any answer is unclear, do not proceed.

---

## Final Note

This architecture is intentionally conservative.

The system is not an autonomous customer-reply agent.
It is a reviewer-only draft generator with strict control-flow boundaries.

Future intelligence is allowed only inside explicit bounded steps, never in orchestration and never in outbound safety checks.
