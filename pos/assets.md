# Assets（判斷資產）

穩定可復用的判斷模式。

## Optimize For Cognitive Compression

**模式**

- AI 時代的主要成本是理解、約束、收斂與驗證，而不是生成。
- 更少 lines 不一定代表更少 complexity；formal completeness 也可能掩蓋 structural bloat。

**規則**

- 先刪除、再合併、再要求剩餘內容證明必要性。
- 優化 fewer concepts、clearer ownership 與 easier mental simulation，而不是 raw line count。

**條件**

- 除非明確接受 behavior change，否則 behavior 保持等價。
- 變更應移除真實 concepts、branches 或 indirection，而不只是 whitespace。
- 保留的 rule 或 abstraction 應可被未來任務直接調用，並具有清楚邊界。

**邊界**

- 不要為了減少 files 或 lines 而 inline 有意義的 concepts。
- 不要保留已不再承載有用意義的 abstractions。
- 不把一次性感悟、漂亮但不可操作的概念或流暢的 AI output 納入 POS。

**成功標準**

- 用更少 concepts 承載更多有效行為。
- Ownership、execution order 與 review path 更容易理解。
- Future patch risk 與重複思考降低。

## Semantic Compression Over Defensive Completeness

**模式**

- AI-generated code 常為了顯得 robust，而新增 local guards、fallback branches、exception blocks 與 logs。
- 這會讓 code 在局部看似合理，卻讓整體系統更難理解。
- 在 personal/internal tools 中，過度 fallback handling 往往只是在保護可恢復的 operator mistakes，卻讓 normal path 更難被心智模擬。

**規則**

- 只有當 defensive code 保護真實 system boundary 時，才新增它。
- 優先使用能保留行為、並讓 runtime model 更容易心智模擬的最小結構。
- 對可恢復的 manual workflow mistakes，優先接受 visible crash，而不是堆疊 guardrail bloat。

**條件**

- Guard 能防止 data loss、duplicate execution、financial loss、irreversible action、silent corruption 或 hard-to-diagnose failure。
- Fallback 符合 business semantics。
- Log 能幫助未來診斷，而不是重複明顯狀態。
- Branch 代表真實 decision，而不是 theoretical completeness。
- 如果允許 visible crash，反而會讓該 failure 更難診斷。

**邊界**

- 不要移除 destructive operations、external side effects、money、file movement 或 silent data corruption 周圍的保護。
- 不要只因 API 可能失敗就新增 `try/except`；如果 failure 應停止當前 operation，就讓它停止。
- 當 default route 已能安全處理 invalid output 時，不要新增 invalid-result branches。
- 不要只為處理 skipped intermediate CLI steps、missing trace artifacts 或其他可恢復 personal workflow mistakes，而新增冗長 fallback code。
- 對 real file mutation、scope boundaries、destructive actions、irreversible actions、silent corruption、money 或 external side effects，保留 hard guards。

**成功標準**

- Code 更容易解釋。
- Failure behavior 是有意識設計的。
- Normal path 保持可見。
- Fallback 是 business-semantic，而不是 generic panic handling。
- Personal workflow mistakes 會足夠 loud 地失敗，方便修正，且不需要新增冗長 defensive branches。

## Extract Helpers Only From Real Reuse

**模式**

- AI 容易把小範圍 local duplication 過度抽象成 helpers。

**規則**

- 除非 helper 能降低總體 cognitive complexity，否則不要引入。

**條件**

- Helper 被真實重複 call sites 使用。
- Helper 命名一個有意義的 concept。
- Helper 降低未來 edit risk。
- Helper 隱藏 mechanical detail，而不是 business decisions。

**邊界**

- 不要只為隱藏兩三行 local code 而新增 helper。
- 如果 reader 需要跳轉更多地方才能理解 flow，不要新增 helper。
- 不要把 workflow-specific logic 提取到模糊的 shared helper file。

**成功標準**

- Extraction 後 top-level flow 更容易閱讀。
- Helper 具有 low-context inputs。
- Helper 不需要大量 parameters 才能重建 hidden context。

## No Custom OOP Unless External Interface Requires It

**模式**

- Personal code 應預設使用 explicit functions、small modules 與 plain data structures。
- Custom classes 常隱藏狀態，並誘發 Manager、Service、Controller-style inflation。

**規則**

- 除非 external library 或 framework 要求 class、subclass、handler 或 callback object，否則不新增 custom object-oriented classes。

**條件**

- 只有 external code 要求 class-shaped interface 時，class 才允許存在。
- Class 必須保持 thin adapter。
- Core business logic 應盡量留在 explicit functions 中。

**邊界**

- 不要為 architecture neatness、grouping functions、lifecycle wrapping、state containers、managers、services、controllers、orchestrators、registries 或 future extensibility 而新增 classes。
- 不要用藏在 dict 裡的巨大 implicit object 取代 class。
- 若需要 shared runtime state，優先使用 plain concrete names 與可見 function inputs/outputs。

**成功標準**

- 閱讀 top-level flow 時，不需要追蹤 `self.*` chains。
- AI patching 無法在沒有硬理由的情況下引入 abstraction layers。
- Runtime ownership 保持 explicit 且可局部 review。

## Treat Public Function Signatures As Contracts

**模式**

- AI refactor 常意外改變 interface shape。

**規則**

- 在改動 public 或 test-facing function signature 前，先識別可能 callers 與 compatibility impact。

**條件**

- 如果 compatibility 重要，保留舊 entry point。
- 將 intentional breaks 明確化。
- 當 behavior 或 interface shape 合法改變時，同步更新 evaluation code。

**邊界**

- Internal-only functions 可以更自由地改動。
- Public、test-facing 或 cross-module functions 需要更嚴格 review。

**成功標準**

- Evaluation failures 反映真實 behavior changes，而不是 accidental interface drift。
- Callers 保持一致。
- Compatibility shims 是有意識且暫時的，而不是 accidental clutter。

## Keep Pipeline Extension Boundaries Explicit

**模式**

- 在 foundation 乾淨前新增 feature，常會創造另一個 special case。
- Runtime services、file routes、workers、processors 與 model policies 常被混入同一層。
- 即使 code 能運作，命名錯誤的 components 仍會造成反覆 design confusion。

**規則**

- 先修 extension surfaces，再新增 extension features。
- 在 reshape code 前，先區分 pipeline concepts。
- 當現有名稱描述 mechanism，而不是 responsibility 時，重新命名 component。

**條件**

- Runtime service：long-lived loop、watcher 或 scheduler。
- File route：從 folder 或 file type 到 queue 的 intake path。
- Worker：queue consumer。
- Processor：single-job executor。
- Model policy：model list、routing behavior、merge behavior 與 distillation behavior。
- Feature 會複製 special case，而不是遵循乾淨 template。
- 名稱迫使人反覆解釋。
- 名稱隱藏 ownership、boundary 或 lifecycle。

**邊界**

- 不要阻擋小 bug fixes。
- 不要把 foundation cleanup 當作 unlimited refactor 的藉口。
- 不要建立 heavy framework abstractions。
- Conceptual separation 可以先於 file separation 存在。
- 不要隨意重新命名 stable public APIs。
- 不要只因 style preference 重新命名。

**成功標準**

- Route、queue、worker、processor 與 model policy responsibilities 清楚可見。
- Toggles、queues 與 names 符合 operator 的 mental model。
- 下一個 feature 有清楚 insertion point，不需要另一條 one-off path。

## Repo Polish Agent Pattern

**模式**

- 在允許 repo-changing automation 前，使用有邊界的 judgment loop。

**流程**

- Plan：根據 task context、long-term rules 與 allowed files，生成 minimal patch plan。
- Review：依照 task constraints、boundaries 與 risks 批判 plan。
- Revise：產生乾淨、可接受或可拒絕的 standalone plan。
- Accept：human approval 將被選定的 plan 提升為 accepted execution artifact。

**邊界**

- Runtime trace 是 evidence，不是 stable asset。
- Accepted plans 是 execution artifacts，不會自動變成 permanent rules。
- Long-term assets 應包含 reusable patterns，而不是 temporary run outputs。
- Model output 必須與 human acceptance 保持分離。

**規則**

- Planning 與 execution 保持分離。
- Review 與 revision 保持分離。
- Model output 與 human acceptance 保持分離。
- Plan 被接受前，不自動化 repo edits。
- Agent capability 不應比 judgment loop 的品質擴張得更快。

**成功標準**

- Plan 是 minimal。
- Touched files 明確。
- Behavior-preservation boundary 清楚。
- Risks 與 stop conditions 已說明。
- Verification commands 已包含。
- Accepted plan 足夠乾淨，可指導 human execution 或 future patch agent。

## Complex Problem Checklist

面對複雜問題時，先定義：

1. 這是什麼 system/object？由誰處理？
2. 需要改變哪個 state？Input 與 output 是什麼？
3. 什麼算好、什麼算壞？Success criterion 是什麼？
4. 哪些 variables、constraints 與 domain rules 會影響結果？
5. 最高風險的 real-world failure points 是什麼？
6. 根據 risk、cost 與 expected return，最小的優先 action 是什麼？
7. 如何 test、review、rollback 或 retry？
8. 完成後能提取什麼 reusable rule、template、failure case 或 example？

## Archive By Reuse Evidence, Not Immediate Taste

**模式**

- 人的即時判斷常會過度保存有吸引力的 ideas，卻低估無聊但可復用的 patterns。
- 當 input volume 增加時，manual archive decisions 會變得不穩定。

**規則**

- Archive promotion 應基於 reuse evidence、feedback、transferability、failure records 與 long-term value。
- Human 應定義 objective、constraints、evaluation 與 final approval，而不是手工控制每個 category。

**條件**

- 該 item 已在重複 tasks 中出現。
- 它改善了未來 judgment 或 action。
- 它有清楚的 retrieval conditions。
- 它有已知的 failure boundaries。

**邊界**

- 沒有 human approval，不要自動化 final promotion。
- 不要只因 raw material 感覺有趣就保存它。
- 不要讓 archive logic 變成複雜的 taxonomy project。

**成功標準**

- Archive 變得更容易 search、reuse、delete 與 improve。
- Promotion 依據 evidence，而不是 mood。
