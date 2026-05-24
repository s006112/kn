# Context（上下文）

## 當前焦點

- 建立輕量級個人作業系統（`pos`）。
- 探索 AI 輔助的判斷資產累積。
- 保持系統最小、低摩擦，並由真實任務驅動。
- 在新增 feature routes 前，先清理專案基礎。
- 將實際 code work 視為可復用 code-iteration 判斷的證據。

## 當前關切

- 避免 over-engineering。
- 避免 rule explosion。
- 優先保留小型可復用模式。
- 避免 AI 生成 helper、function、class 膨脹。
- 保持明確的 runtime 邊界。
- 讓 code iteration 專注於降低認知負荷，而不只是減少行數。
- 在底層 extension surface 能乾淨承接之前，不新增 feature routes。
- 區分 runtime services、file routes、queues、processors 與 model policies。

## 活躍專案方向

- 暫停非必要 feature expansion。
- 先清理 pipeline foundation。
- 釐清 runtime services 與 business pipelines 的邊界。
- 將 scanner-like components 重新定義為 file intake 與 queue routing services。
- 在新增更多 routes 前，讓未來 route expansion 變得可預期。

## 當前 Pipeline 清理目標

- Scanner services 只負責發現 files 並 enqueue 已啟用的 routes。
- Workers 只消費自己的 queues。
- Processors 只處理單一 job type。
- Config 區分 route、folder、model list 與 processing policy。

## 近期優先順序

1. 重新命名並釐清 scanner-service 語義。
2. 讓 intake scanning 遵守已啟用的 route toggles。
3. 將 intake logic 拆成清楚的 route-level sections。
4. 清理 route、folder、model 命名。
5. 在 foundation 乾淨後，再重新評估 feature routing。
