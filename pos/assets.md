# Assets（判斷資產）

穩定可復用的判斷模式。

## Core Operating Principles

### Complexity Compression First

Pattern:
- AI 時代的主要成本不再是生成，而是理解、約束、收斂與驗證。
- AI 生成物只是候選毛坯，不應直接進入長期系統。
- 人的核心責任是保留語義主權：確認因果、守住不變式、降低認知負荷。

Rule:
- 處理任何 AI 生成物時，先刪除、再合併、再要求剩餘部分證明必要性。
- 無法說清「非它不可」的內容，默認不保留。

Criteria:
- 是否降低未來認知負荷？
- 是否改善判斷質量？
- 是否能被未來任務直接調用？
- 是否有清楚邊界？
- 是否避免概念膨脹？

Boundary:
- 不保留漂亮但不可操作的概念。
- 不把一次性感悟升級成穩定規則。
- 不因為 AI 輸出流暢就把它納入 POS。

Success criteria:
- 用更少概念承載更多有效行為。
- 未來 review 更快、更準、更少重複思考。


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

## Optimize For Cognitive Compression, Not Raw Line Count

**模式**

- 減少行數可能造成 semantic loss。
- 增加行數也可能用 formal completeness 掩蓋 structural bloat。

**規則**

- 優化 fewer concepts、clearer ownership 與 easier mental simulation。

**條件**

- Runtime ownership 變得更清楚。
- Execution order 變得更容易跟隨。
- 除非明確接受 behavior change，否則 behavior 保持等價。
- 變更移除真實 concepts、branches 或 indirection，而不只是 whitespace。

**邊界**

- 不要為了減少 files 或 lines 而 inline 有意義的 concepts。
- 不要保留已不再承載有用意義的 abstractions。
- 除非 code growth 換來更清楚的 boundary 或更安全的 behavior，否則不要接受。

**成功標準**

- Code 變得更短，或 conceptually smaller。
- Future patch risk 降低。
- Reviewer 能用更少 moving parts 解釋 flow。

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

## Build Extension Foundation Before Extension Features

**模式**

- 在 foundation 乾淨前新增有用 feature，常會創造另一個 special case。

**規則**

- 先修 extension surfaces，再新增 extension features。

**條件**

- New feature 需要 route、queue、processor、model group、folder 或 runtime thread。
- 既有 ownership boundaries 不清楚。
- Feature 會複製 special case，而不是遵循乾淨 template。

**邊界**

- 不要阻擋小 bug fixes。
- 不要把 foundation cleanup 當作 unlimited refactor 的藉口。
- 只有當 feature 暴露真實 structural debt 時，feature pause 才合理。

**成功標準**

- New routes 可以依照既有 pattern 新增。
- Route、queue、worker、processor 與 model policy responsibilities 清楚可見。
- 下一個 feature 不需要另一條 one-off path。

## Separate Pipeline Concepts Before Optimizing Code Shape

**模式**

- Runtime services、file routes、workers、processors 與 model policies 常被混入同一層。

**規則**

- 在決定 split files、extract helpers 或 rewrite flow 前，先區分 concepts。

**條件**

- Runtime service：long-lived loop、watcher 或 scheduler。
- File route：從 folder 或 file type 到 queue 的 intake path。
- Worker：queue consumer。
- Processor：single-job executor。
- Model policy：model list、routing behavior、merge behavior 與 distillation behavior。

**邊界**

- 不要建立 heavy framework abstractions。
- 不要只為 visual neatness split files。
- Conceptual separation 可以先於 file separation 存在。

**成功標準**

- 每個 component 都能按 responsibility 命名。
- Toggles 與 queues 符合 operator 的 mental model。
- Future route expansion 有清楚 insertion point。

## Name Components By Responsibility

**模式**

- 即使 code 能運作，命名錯誤的 components 仍會造成反覆 design confusion。

**規則**

- 當現有名稱描述 mechanism，而不是 responsibility 時，重新命名 component。

**條件**

- 名稱迫使人反覆解釋。
- Component 被誤認為另一個 architectural layer。
- 名稱隱藏 ownership、boundary 或 lifecycle。
- 更好的名稱能降低 future patch risk。

**邊界**

- 不要隨意重新命名 stable public APIs。
- 不要只因 style preference 重新命名。
- 只有在 semantic clarity 能改善 future maintenance 時才重新命名。

**成功標準**

- 名稱告訴 reader 這個 component 擁有什麼。
- Scheduling mechanism 不偽裝成 business purpose。
- Future patches 較不容易把 logic 掛到錯誤 layer。

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

## Complex Problem Methodology

**規則**

面對複雜問題時，不要直接跳到 solution。

先定義：

1. 這是什麼 system/object？
2. 哪個 state 需要被改變？
3. Success criterion 是什麼？
4. 哪些 variables 會影響結果？
5. 哪些 constraints 不能被違反？
6. 可能出現哪些 real-world scenarios？
7. 最高風險的 failure points 是什麼？
8. 根據 risk、cost 與 expected return，應先做什麼？
9. 如何驗證結果？
10. 完成後能提取什麼 reusable rule、template 或 checklist？

## Archive Learning Strategy

**規則**

Archive 不應由人類即時主觀判斷主導，而應由系統根據 task reuse、result feedback、transferability、failure records 與 long-term value 自動學習保存策略。

人類保留 objective、constraint、evaluation 與 final approval，而不是手工控制每個分類與取捨。

## Guidance / Verification Checklist

### 1. Learning Engine

- 這個任務由誰處理：LLM、script、human 還是 agent？
- 它需要吸收什麼 input？
- 它產出什麼 output？

### 2. Guidance Layer

- 什麼算好？
- 什麼算壞？
- 什麼錯誤必須避免？
- 有哪些 domain-specific rules？

### 3. Verification Layer

- 如何驗證 output？
- 有沒有 test、compiler、rubric 或 review checklist？
- 失敗時如何 rollback 或 retry？

### 4. Asset Loop

- 這次經驗能沉澱成什麼？
- Rule？
- Template？
- Failure case？
- Reusable example？

