# POS Agent 規則

此資料夾保存可復用判斷資產，而不是普通筆記。

## 核心取向

| 原則 | 在 POS 中的落地 |
| --- | --- |
| 精良單元 | 每個 principle、rule、concept 都要能被調用，不只是被收藏。 |
| 簡潔結構 | 少分類、少層級、少概念膨脹，保留最短調用路徑。 |
| 冗餘空間 | 保留 review、distill、merge、delete 的節奏，不讓系統塞滿垃圾。 |

## 作業規則

- 保持 context、decisions、proposals 與 stable assets 之間的邊界。
- 未經明確人工批准，不得將 proposals 提升為 assets。
- 不要把臨時實作細節轉成永久規則。
- 除非重複使用證明有必要，不要擴張 POS 結構。
- 優先保留小而持久的判斷模式，而不是大型理論框架。
- 不確定時，先把內容放入 proposal，而不是 asset。

## Asset 品質

穩定 asset 必須幫助未來 code review、重構、專案方向判斷或 AI-agent 行為。

每個 asset 通常應包含：

- Pattern
- Rule
- Criteria
- Boundary
- Success criteria

條目應保持短小、有邊界、可復用。

## Code-Iteration 捕捉

只從真實 code work 中提取規則。

不要把每次挫折都保存為規則。只捕捉可能重複出現，且可能改善未來決策的部分。

需要區分：

- runtime trace 與 accepted artifact
- accepted decision 與 reusable rule
- local implementation fact 與 general pattern
- proposal 與 stable asset

## AI-Agent 紀律

AI 必須優化 semantic compression，而不是 defensive verbosity。

在新增 code、helpers、wrappers、classes、branches、validations 或 error handling 前，先識別真正需要保護的責任。

- 拒絕只有局部形式完整、但沒有系統價值的變更
- 拒絕只減少行數、卻增加導航成本的 helper functions

預設採用最小的 behavior-preserving change，讓 runtime model 更容易被心智模擬。
