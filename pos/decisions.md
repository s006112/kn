# Decisions（已接受決策）

已接受的決策，以及其原因與邊界。

## 2026-05-08 - 將 `pos` 保留在現有 private repo 中

**決策**

- 先在現有 private repo 中建立 `pos`，而不是建立新 repository。

**原因**

- 降低摩擦。
- 立即開始使用。
- 避免 premature architecture。
- 優先真實使用，而不是結構純度。
- 讓系統通過真實任務緩慢演化。

**邊界**

- 在重複使用證明有明確收益之前，不將 `pos` 拆成獨立 repo。

## 2026-05-09 - 先將 code-iteration principles 捕捉為 proposals

**決策**

- 從真實 refactor work 中提取 code-iteration principles 時，先放入 proposals，再考慮提升為 stable assets。

**原因**

- 這些 principles 來自真實 code review 與 cleanup。
- 它們有用，但需要重複驗證。
- Stable assets 不應過快擴張。

**邊界**

- 經過重複驗證的 patterns，可在人工批准後提升為 assets。

## 2026-05-09 - 不要過早提取 agent-specific helpers

**決策**

- 在 agent 仍然很小且 read-only 時，將 agent workflow helpers 保留在 `agent/agent.py` 中。
- 只有當至少兩個真實 call sites 出現重複使用時，才提取 helper。
- 通用 file IO helpers 可以放入 shared helper modules。
- Agent-specific parsing、POS loading 與 prompt assembly 應靠近 agent workflow。

**原因**

- 避免模糊的 helper collections。
- 保留運行流程的局部可讀性。
- 防止因視覺整潔而不是實際復用導致 helper sprawl。
- 讓 shared helpers 僅限於穩定、通用、低上下文的操作。

**邊界**

好的 helper extraction：

- 通用 text read/write
- optional file read
- 安全 filename 或 path handling
- 被多個 modules 使用的重複低上下文 utilities

壞的 helper extraction：

- single-caller parsing helpers
- 沒有第二個 caller 的 POS-loading helpers
- 隱藏 workflow decisions 的 prompt assembly helpers
- 需要大量 parameters 才能重建 local context 的 helpers

**規則**

- 從真實重複中提取，而不是從想像中的未來復用中提取。

## 2026-05-09 - 建立 repo polish agent loop

**決策**

- 將 repo polish agent loop 建立為 Plan -> Review -> Revise -> Accept。
- 在新增 execution automation 前，讓 agent 保持 planning 與 judgment mode。
- 將 `agent/final_plan.md` 視為人工接受的 execution artifact。
- 將 trace files 視為 runtime evidence，而不是 stable assets。

**原因**

- 目標不是華麗的 autonomous coding agent。
- 目標是結構化、可 review、可復用的 repo polishing。
- 在 patch execution 自動化前，planning quality 必須先穩定。
- Human approval 仍然是 model output 與 repo-changing action 之間的 gate。

**邊界**

Agent 可以：

- 讀取 task context
- 讀取 POS context
- 讀取 allowed repo files
- 生成 minimal patch plan
- review plan
- revise plan
- 在 human approval 後保存 accepted final plan

Agent 不得：

- 自動編輯 repo files
- 自動 apply patches
- 自動將 runtime trace 提升為 POS assets
- 擴張到 allowed files 之外的範圍
- 在沒有 human approval 時將 model output 視為 accepted

**規則**

- 在 planning loop 能穩定產生乾淨、有邊界、可驗證的 plans 之前，不新增 execution automation。

## 2026-05-10 - 在 foundation 乾淨前暫停 GOSSIP extraction shortcut

**決策**

- 暫停計畫中的 GOSSIP extraction shortcut。
- 先清理 pipeline foundation。
- 在 intake、queue、worker 與 processor 邊界更清楚之前，將 route expansion 視為 blocked。

**原因**

- 這個 shortcut 方向上有用，但目前會增加 structural debt。
- Runtime 混合了 scanner service、file intake route、processing worker、queue ownership 與 model policy。
- 現在新增 route 會創造另一個 special case，而不是可復用的 extension pattern。

**邊界**

- 暫不新增 GOSSIP routing。
- 不在 foundation cleanup 中改動 pretext、extract、audio 或 ttml processing internals。
- 先釐清 orchestration、scanner semantics、route enablement 與 naming。
- Foundation 能乾淨承接後，再恢復 feature routes。

**規則**

- 先建立 extension foundation，再新增 extension features。

## 2026-05-10 - 將 `PeriodicScanner` 視為 file intake，而不是 business pipeline

**決策**

- 將 `PeriodicScanner` 視為命名不當的 runtime service，而不是 business pipeline。
- 將它重新框定為 file intake 與 queue routing。

**原因**

- 它供給多個 pipelines，而不是處理單一 business route。
- Workers 停用時 scanner 仍 enqueue files，會造成語義不一致。
- 這個名稱描述的是 timing behavior，而不是 responsibility。
- 未來 routes 需要清楚的 intake location。

**邊界**

- 概念上往 file intake responsibility 重新命名。
- Scanner 只應發現 files，並 enqueue 已啟用的 routes。
- Workers 只應消費 queues。
- Processors 只應處理 jobs。
- 不要讓 scanner 變成 processor。

**規則**

- 依 responsibility 命名 runtime components，而不是依 scheduling mechanism 命名。

## 2026-05-10 - 預設禁止 custom OOP

**決策**

- 為個人 codebase 建立嚴格的 no-custom-OOP 規則。
- 唯一例外是 external library 或 framework 要求 class、subclass、handler 或 callback object。

**原因**

- 保持 execution flow 明確。
- 防止 AI 生成 Manager、Service、Controller 抽象層。
- 避免將狀態隱藏在 `self.*` 後面。
- 改善局部可讀性、patch review 與長期 maintainability。

**邊界**

- 不要為了 grouping functions、architecture neatness、lifecycle wrappers、state containers、orchestrators、registries 或 future extensibility 而使用 custom classes。
- 如果 class 是 external interface 強制要求的，保持它很薄，並將核心邏輯推回 functions。

**規則**

- 除非 external interface 要求，否則不使用 custom OOP。

## 2026-05-12 - 將 defensive code 視為 semantic boundary decision

**決策**

- 不讓 AI 預設新增 defensive branches、fallback paths 或 exception handling。
- 每個 defensive structure 都必須說明它保護的真實 boundary。

**原因**

- AI coding 常製造局部形式完整，卻增加全局複雜度。
- 許多 guards、logs 與 invalid-state branches 並不保護真實 data 或 behavior。
- 目標是讓 code 更容易心智模擬，而不是讓 code 在局部看起來安全。

**邊界**

- 當 defensive code 保護 data integrity、irreversible actions、external side effects、financial loss、duplicate execution 或 silent corruption 時，保留它。
- 當 default route 已能安全處理該情況時，移除或拒絕 defensive code。
- Fallback 應表達 business semantics，而不是 generic fear。

**規則**

- Defensive code 必須通過保護真實 system boundary 來證明自己必要。
