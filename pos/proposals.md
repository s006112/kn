# Proposals（候選規則）

只保留尚未穩定、但值得下一輪驗證的候選規則。

## Promotion Gate

- 只有在重複驗證並獲得明確人工批准後，才提升 proposal。
- 提升前先確認：是否已有 reuse evidence、是否有清楚 boundary、是否能 merge 到現有 asset。
- 若 stable asset 已能約束相同 decision，delete proposal，不新增同義規則。

## Promotion Review Queue

| Proposal | 建議 | 若提升，優先放置位置 |
| --- | --- | --- |
| 001 - Explicit runtime state | pending：需要更多 reuse evidence | Merge into `No Custom OOP Unless External Interface Requires It` |
| 002 - Intake follows route enablement | ready for promotion review | Merge into `Keep Pipeline Extension Boundaries Explicit` |
| 003 - Critical operational guarantees | pending：需要跨任務 evidence | New project-judgment asset only if repeatedly useful |

## Proposal 001 - Keep Runtime State Explicit

**候選規則**

- 優先使用 explicit dependency passing，而不是 module-level mutable state。
- 只有當 named fields 能比鬆散 raw values 更清楚表達 ownership 時，才使用 lightweight data bundle。

**提升條件**

- 多個真實 refactors 都因 hidden state 難以追蹤而受阻。
- Bundle 能避免脆弱 tuple ordering 或過長的 loose-argument passing。
- Execution flow 仍可由 function inputs 與 outputs 直接理解。

**邊界**

- 真正的 process singleton interface 或 compatibility shim 可以暫時保留 global state。
- 不要建立 generic `context` bag、behavior-heavy class 或 hidden state container。
- 若 signature change 影響 public callers，遵守 public-contract asset。

## Proposal 002 - Intake Follows Route Enablement

**候選規則**

- Intake 只為 enabled routes enqueue 工作。
- Disabled route 不建立 backlog，除非明確啟用 dedicated backlog-building mode。

**提升條件**

- Toggle behavior 應符合 operator 的 mental model。
- Scanner、queue 與 worker 的 ownership 已區分。
- Always-on runtime service 與 route intake 已明確分開。

**邊界**

- 規則適用於 intake routes，不自動套用到所有 background services。
- 有明確目的時，可以保留獨立的 backlog-building mode。

**Evidence**

- 已有 scanner cleanup 的具體 evidence。

## Proposal 003 - Design For Critical Operational Guarantees

**候選規則**

- 不只完成表面任務；先識別 failure cost、trust requirement、time pressure 與 system dependency。
- 當 failure cost 高時，將 non-standard work 轉成可複製、可驗證、可交付的保障系統。

**提升條件**

- 此判斷已在多個真實 project 或 customer decisions 中改善 priority selection。
- 能指出具體 failure mode、驗證方法與 delivery boundary。
- 能區分普通執行工作與值得系統化的 critical operation。

**邊界**

- 不要把每個 manual workflow 都描述成 mission-critical system。
- 不要保留只有策略語氣、卻無法改變 decision 的 slogan。

**Review**

- 目前是有方向性的 project-judgment proposal，不是 code-refactor rule。
- 需要跨任務 evidence 後，再考慮建立獨立 asset。

## Removed After Distillation

- Stage-level error centralization：由 defensive-boundary asset 與 real-reuse helper asset 共同約束。
- Operational route naming：已由 pipeline-extension asset 的 responsibility naming 規則涵蓋。