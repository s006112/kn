# ALI Email Review System

ALI 是 reviewer-only email draft generator。

它不会直接回复 customer，也不会自动对外发信。Reviewer 把邮件转发给 ALI；ALI 只把 internal draft 回给同一个 reviewer，并可按 `ADMIN_USERNAME` 加内部 audit/admin Bcc；最终是否采用由人决定。

## Run

```bash
python -c "from ali.ali_email import main; main()"
```

## Source Of Truth

本文是 ALI 的精简 architecture contract。Module docstring 只保留 ownership 摘要并指向本文；局部 code comment 可说明附近实现约束，但不要复制整段 contract。

## Core Invariants

1. **Reviewer-only delivery**: outbound `To:` 必须是原 forwarding reviewer；`ali/ali_send.py` hard-block mismatch。
2. **No external recipients**: customer / third-party address 永远不是合法 `To`、`Cc`、`Bcc`。`Cc` 禁止；`Bcc` 只允许适用时的 `ADMIN_USERNAME`。
3. **Human-only entry**: accepted sender 必须是内部 `@ampco.com.hk`；拒绝 `ali@ampco.com.hk` 以防 loop。
4. **Reserved review namespace**: subject 含 `[ALI:vN]` 只属于 ALI review thread。
5. **Silence means reject**: reviewer 空回复 = REJECT，然后 mark SEEN。
6. **Reviewer text means revise**: reviewer reply text 非空，则基于 `previous_draft + P0-cleaned reviewer instruction` 生成下一版。
7. **Model output is content only**: P1/P2 LLM/RAG output 是 main reply content；final greeting/closing 由 deterministic composer 生成。Model output 不能决定 routing、recipient、retry、mailbox state、reject 或 message movement。

## Runtime Shape

入口：`ali/ali_email.py::main()`。

ALI 每轮 poll Phase 1 和 Phase 2；之后按香港工作时间 sleep：09:00-18:00 为 1 分钟，否则 5 分钟。

| Term | Meaning |
| --- | --- |
| Phase 1 | Reviewer 新转发给 ALI 的邮件。 |
| Phase 2 | Reviewer 回复既有 `[ALI:vN]` review thread。 |
| v1 | Phase 1 生成的 initial draft。 |
| v2+ | Phase 2 根据 feedback 生成的 revised draft。 |

### Phase 1

`fetch_new_messages(max_messages=2)` 抓 UNSEEN message，执行 sender rules，跳过 `[ALI:vN]` subject，生成 v1，回给 forwarding reviewer；send 成功后才 mark source message SEEN。

### Phase 2

`fetch_sender_replies()` 抓 subject 命中 `[ALI:v` 的 UNSEEN message；继续执行 sender rules。Reviewer-authored text 为空即 REJECT 并 mark SEEN。非空则 extract text、parse latest review block、走 edit-only path 生成 vN+1、回给 reviewer，然后 mark SEEN。

## Pipeline Steps

`Step0 -> Step1 -> Step2 -> Step3 -> Step4 -> Step5`

### Step0: Normalize And Parse

Owner: `ali/ali_parse.py`

负责 subject/body normalization、body size limit、reviewer-authored reply extraction、latest review version/draft parsing、review protocol constants。不得 route、retrieve、call LLM、send mail 或 mark mailbox state。

### Step1: Route

Owner: `ali/ali_llm.py::route_email()`

只为 v1 RAG gating 做 deterministic category selection。当前 category：`safety`、`rita`、`unknown`。Routing 不生成内容，也不决定 final answer。

### Step2: Retrieve

Owner: `ali/ali_llm.py::rag_retrieval()` with `rag/helper_rag_pipeline.py`

Current RAG map:

| Category | Engine |
| --- | --- |
| `safety` | `standard` |
| `rita` | `rita` |
| other | no RAG |

Retrieval failure degrade to no-context generation。RAG 只服务 v1；v2+ 必须 bypass routing and retrieval。

### Step3: Generate

Owner: `ali/ali_llm.py::generate_review_package()`

v1 path：normalize input，组 `email_text`，用 `prompt_ali_p0_extraction.txt` 抽取 `query_body`；用 `(subject_norm, query_body)` route，用 `query_body` retrieve/generate。RAG 有 answer 则用 answer，否则 call `prompt_ali_p1_system.txt`。

v2+ path：必须有 `previous_draft`；先 strip previous draft 的 greeting/closing，并用 `prompt_ali_p0_extraction.txt` 清理 reviewer instruction；再用 `prompt_ali_p2_revision.txt` 生成 revised main reply content only。不得 reroute、retrieve 或 fallback to rewrite semantics。

### Step4: Review Hook

Owner: `ali/ali_llm.py::step4_review()`

当前 disabled，直接返回 draft。未来只可 refine draft；不得 reroute、retrieve、call LLM、加新事实或改变 control flow。

### Step5: Package

Owner: `ali/ali_llm.py::render_review()` and `ali/ali_email.py`

先 strip accidental greeting/closing，再 compose `Hi {from_name}, ... Regards, Ali`，之后加 ALI review header/footer，并分配 `[ALI:vN]` subject version。Packaging 不得改变 delivery policy。

## Module Ownership

| Module | Owns |
| --- | --- |
| `ali/ali_email.py` | orchestration、phase sequencing、guarded execution、lifecycle、subject versions、failure quarantine |
| `ali/ali_fetch.py` | IMAP fetch、sender allowlist、admin bypass、raw mail to `EmailMessage` |
| `ali/ali_parse.py` | normalization、reviewer reply extraction、review-state parsing、protocol constants |
| `ali/ali_llm.py` | route selection、RAG gating、v1 generation、v2+ edit-only generation、review hook、rendering |
| `ali/ali_send.py` | reviewer-only SMTP delivery、recipient enforcement、best-effort Sent append |
| `rag/helper_rag_pipeline.py` | RAG engine execution and answer/context assembly |

Rule of thumb: semantic logic belongs lower in the pipeline, not in `ali_email.py`。

## Failure And Mailbox Rules

- Phase-level failure：log 后下轮 retry。
- Message-level deterministic failure（`ValueError`、`FileNotFoundError`）：move to `Ali_failed`。
- Transient send/fetch/runtime failure：保持 UNSEEN 以便 retry。
- Disallowed sender：fetch layer 可移出 active path。
- Normal message：terminal action 成功后才 mark SEEN。

## Evolution Rules

Allowed：改进 route rules、prompts、RAG quality、v1/v2 generation behavior、parsing correctness、future bounded Step4 refinement。

Careful：保持 `ali_email.py` stable；主要只为 orchestration bug、invariant enforcement 或 cleanup 修改。

Forbidden:

- customer/external recipients；
- 在 `ali_email.py` 放 routing、RAG、prompt construction 或 content decision；
- model output 控制 runtime behavior；
- v2+ rerouting 或 RAG；
- weakening `ali_send.py` recipient checks；
- 在 ALI review thread 以外复用 `[ALI:vN]`；
- 把 runtime policy 藏进 prompt。

## Change Gate

Architecture change 前确认：

1. 是否保持 reviewer-only delivery？
2. 是否保持 model output 不控制 runtime？
3. 是否保持 Phase 1 / Phase 2 边界？
4. 是否保持 v2+ edit-only？
5. 是否避免 semantic logic 上移到 orchestration？
