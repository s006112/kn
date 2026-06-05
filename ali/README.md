# ALI Email Review System

ALI 是一個 reviewer-only email draft generator。

它不會直接回覆 customer，也不會自動對外發信。  
所有 generated draft 的 `To:` 只會回給 forwarding reviewer，並可按 `ADMIN_USERNAME` 加內部 audit Bcc；由人決定是否採用。

## Run

```bash
python -c "from ali.ali_email import main; main()"
```

## Document Ownership

本文是 ALI email review system 的唯一 **architecture source of truth**。完整的 active runtime、hard invariants、Phase / Version 定义、Step boundary、module ownership 和 evolution rules 只在这里维护。

Python module docstring 只保留职责摘要、本文链接和 `Used by:`。与实现直接相关的局部约束可以留在函数附近，但不得复制整段 architecture contract。

ALI 不是 autonomous customer-reply agent；它只是 **reviewer-only draft generator**：内部 reviewer 转发邮件给 ALI，ALI 把 internal review draft 回给同一个 reviewer，并可按 `ADMIN_USERNAME` 加内部 audit Bcc；最终是否对客户发信由人决定。

---

## 1. Core Invariants

1. **No Autonomous External Action**
   不得发信给 customer / external third party；所有 generated content 都是 INTERNAL-ONLY、reviewer-facing。

2. **Forward-Only Reply Model**
   outbound `To:` 必须是原 forwarding reviewer；`ali/ali_send.py` 必须 hard-block `To:` mismatch。
   系统可按 `ADMIN_USERNAME` 加内部 audit Bcc，但不得把 customer address 作为 `To` / `Cc` / `Bcc`。

3. **Silence Means Termination**
   reviewer 空回复 = REJECT；mark SEEN 后停止。

4. **Valid Reviewer Reply Means Revision**
   reviewer 在回覆中寫了新內容 = valid reviewer reply；下一版基於 `previous_draft + reviewer_reply_text` 生成。

5. **Reserved Review Namespace**
   `[ALI:vN]` 只属于 ALI review thread；Phase 1 处理 new forwarded email，Phase 2 处理 reviewer reply。

6. **Human-Only Entry**
   只允许 allowlisted internal sender。当前规则：允许 `@ampco.com.hk`，拒绝 `ali@ampco.com.hk`，避免 self-reply loop。

7. **Model Output Never Controls Runtime**
   LLM 只能生成 draft text，不能决定 routing、recipient、mark SEEN、retry、reject 或 mailbox movement。

---

## 2. Runtime Shape

入口：`ali/ali_email.py::main()`。

### Terminology

`Phase` 表示当前处理哪一种 inbox message；`vN` 表示 draft revision number。它们不是同一套编号。

| Name    | Definition |
| ------- | ---------- |
| Phase 1 | 处理 reviewer 新转发给 ALI 的 email。 |
| Phase 2 | 处理 reviewer 对既有 ALI review thread 的 feedback email。 |
| v1      | Initial draft，由 Phase 1 生成。 |
| v2+     | Revised draft，由 Phase 2 根据有效 reviewer feedback 生成。 |

同一个 review thread 可以多次进入 Phase 2，依次生成 v2、v3、v4 等 revised drafts。

### Phase 1 — New Forwarded Emails

`fetch_new_messages(max_messages=2)` 抓 UNSEEN mail；fetch layer 过滤 sender allowlist、`ADMIN_USERNAME` bypass、reserved review subject；生成 initial draft（v1）；`To:` 只回给 forwarding reviewer，可按 `ADMIN_USERNAME` 加内部 audit Bcc；成功后才 mark original message as SEEN。

### Phase 2 — Reviewer Feedback Emails

`fetch_sender_replies()` 只抓 subject 命中 `[ALI:v` 的 UNSEEN reply；继续执行 allowlist 和 admin bypass；empty body = REJECT 并 mark SEEN；non-empty reviewer reply text = parse last review state + extract reviewer reply text + generate next revised draft（v2+）；成功发送后 mark reply as SEEN。

polling cadence 只是 scheduling，不属于 semantic architecture。

---

## 3. Pipeline Steps

`Step0 -> Step1 -> Step2 -> Step3 -> Step4 -> Step5`

### Step0 — Input Normalization / Review-State Parsing

Owner: `ali/ali_parse.py`

做：normalize subject/body、限制 body size、extract reviewer reply text、解析 last review version/draft、维护 review protocol constants。

不做：routing、retrieval、LLM call、send mail、mark SEEN。

输出给：Step1/Step3；v2+ edit-only path。

### Step1 — Routing

Owner: `ali/ali_llm.py::route_email()`

做：deterministic route selection，只输出 RAG gating 所需的 `category`。

当前 category：`safety`、`rita`、`unknown`。

不做：content generation、LLM call、retrieval、recipient decision、final answer decision。

Routing 只选择 execution routine / constraint，不决定 answer。

### Step2 — Retrieval / Tools

Owner: `ali/ali_llm.py` gates retrieval；`rag/helper_rag_pipeline.py` executes retrieval。

当前 RAG map：

| route.category      | RAG engine |
| ------------------- | ---------- |
| `safety`            | `standard` |
| `rita`              | `rita`     |
| others              | no RAG     |

retrieval failure 必须 degrade to no-context generation。

不做：修改 routing output、send mail、packaging、mark SEEN。

只服务 v1 generation；v2+ edit-only path 必须 bypass routing and retrieval。

### Step3 — Draft Generation

Owner: `ali/ali_llm.py`

做：生成 reviewer-facing internal draft。

v1 rewrite path：normalize input → route → optional RAG → RAG 有 answer 则用 answer 作 draft，否则走 `ali/prompt_ali_p1_system.txt` LLM path。

v2+ edit-only path：必须有 `previous_draft`；使用 `ali/prompt_ali_p2_revision.txt`；通过 `previous_draft + reviewer_reply_text` 表达修改任务；只编辑 previous draft。

v2+ 不得 rerun routing、rerun retrieval、fallback to rewrite semantics。

Step3 不得决定 recipient、mark IMAP state、处理 mailbox protocol、改变 delivery policy。

### Step4 — Review

Owner: `ali/ali_llm.py::step4_review()`

当前 disabled by default，NO-OP，直接返回 draft。

Step4 只能是 post-generation hook；不得 reroute、retrieve、call LLM、改变 control flow、引入新事实或 policy。未来若启用，也只能 refine draft，不能变成 semantic controller。

### Step5 — Packaging

Owner: `ali/ali_llm.py::render_review()` + `ali/ali_email.py` subject/version sequencing。

做：加 ALI review protocol header/footer；分配 review version；生成 `[ALI:vN]` review-thread subject。

不做：改变 draft semantics、routing/retrieval result、recipient decision。

---

## 4. Module Boundaries

| Module                       | Responsibility                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------------- |
| `ali/ali_email.py`           | orchestration、Phase sequencing、guarded execution、subject versioning、message lifecycle |
| `ali/ali_fetch.py`           | IMAP fetch、sender allowlist、ADMIN bypass、raw record → `EmailMessage`                  |
| `ali/ali_parse.py`           | input normalization、reviewer reply extraction、review-state parsing、protocol constants       |
| `ali/ali_llm.py`             | deterministic route selection、RAG gating、v1 generation、v2+ edit-only generation、Step4 hook、review rendering |
| `rag/helper_rag_pipeline.py` | RAG engine execution and answer/context assembly                                      |
| `ali/ali_send.py`            | reviewer-only outbound delivery、forward-sender enforcement、append Sent best-effort    |

核心规则：**logic must not migrate upward**。越靠近 `ali_email.py`，semantic intelligence 越少。

---

## 5. Orchestration Contract

`ali/ali_email.py` 是 STABLE orchestration layer。

允许：two-phase polling、调用 downstream modules、version subject、lifecycle sequencing、`_run_guarded()` exception containment、deterministic failure quarantine to `Ali_failed`、transient failure 保持 UNSEEN retry。

禁止：routing heuristic、quoted history parsing、RAG logic、prompt construction、content decision、delivery policy change。

---

## 6. Fetch Contract

`ali/ali_fetch.py` 只决定哪些邮件可进入 pipeline。

Phase 1：UNSEEN only；跳过 `[ALI:vN]` subject；执行 allowlist；`ALI_DEBUG_MODE=False` 时 bypass `ADMIN_USERNAME`；filter 后再应用 processing cap。

Phase 2：UNSEEN only；subject 必须匹配 `[ALI:v`；执行同样 allowlist / admin bypass。

fetch layer 可移动 disallowed mail away from active path；不得 mark normal processing success、解析 review protocol、生成内容。

---

## 7. Delivery Contract

`ali/ali_send.py` 是 outbound safety boundary。

规则：`To:` 必须等于 original forwarding reviewer addr-spec；可按 `ADMIN_USERNAME` 加内部 audit Bcc；customer address 永远不是合法 `To` / `Cc` / `Bcc`；append to IMAP Sent 是 best-effort，不参与 semantic decision。

禁止：决定 draft content、判断 REJECT / valid reviewer reply、mark source message SEEN。

---

## 8. Evolution Rules

优先保持 system narrow。

可演进：`ali_llm.py::route_email()` 的 route rule；`ali_llm.py` 的 generation / retrieval gating / edit-only behavior；`rag/helper_rag_pipeline.py` 的 retrieval quality；future Step4 module。

谨慎演进：`ali_parse.py` 只为 normalization、reviewer reply extraction、review protocol parsing correctness 改；`ali_email.py` 只做 bug fix、invariant enforcement、orchestration cleanup。

legacy / experiment code 必须 isolated、not referenced by `ali_email.py`、标注 non-authoritative。

---

## 9. Forbidden Anti-Patterns

禁止：

* 在 `ali_email.py` 加 routing logic。
* 混合 parsing、generation、delivery。
* 让 LLM output 决定 control flow。
* v2+ edit-only path 重新 routing 或 RAG。
* 弱化 reviewer-only send guard。
* 绕过 `[ALI:vN]` namespace。
* 把 Step4 偷偷变成 semantic controller。
* 把 code policy 藏进 prompt。

---

## 10. Change Gate

任何 architecture change 前问：

1. 是否破坏 invariant？
2. 是否把 semantic intelligence 往上搬？
3. 是否模糊 Phase 1 / Phase 2？
4. 是否让 model output 影响 runtime control？
5. 是否削弱 reviewer-only safety enforcement？

任一问题说不清，就不要改。

---

## Final Position

ALI 的价值来自 clear boundary，不来自 autonomy。未来可以增加 intelligence，但只能放在 explicit bounded Step 内；不能进入 orchestration，也不能碰 outbound safety check。
