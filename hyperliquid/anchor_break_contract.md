下面是可直接覆盖的 **clean final contract**：

# Anchor Break + Single-Sided Mode Contract v2.1 (Compact)

1. Purpose
   Anchor Break 用於在合法單邊模式下，當唯一殘單偏離 mid grid 過大時，cancel 並以 mid grid 重建系統
   不涉及策略優化、價格跟蹤或動態調整

2. Modes
   系統僅允許以下模式：
   PAIR_MODE：1 BUY + 1 SELL，pair shape 有效
   BUY_ONLY_MODE：僅 1 BUY
   SELL_ONLY_MODE：僅 1 SELL
   ABNORMAL：其他所有情況

   BUY_ONLY_MODE 與 SELL_ONLY_MODE 為嚴格鏡像、等價狀態

3. Residual 定義
   Residual = 系統中唯一剩餘且未成交的掛單
   僅在 BUY_ONLY_MODE 或 SELL_ONLY_MODE 下成立

4. Stale 定義
   僅對 Residual 判斷

   BUY_ONLY：mid - buy_price ≥ N × GRID_STEP
   SELL_ONLY：sell_price - mid ≥ N × GRID_STEP

   否則為 non-stale

5. Rebuild 類型
   fill-driven rebuild：因 BUY 或 SELL 成交觸發
   stale-driven rebuild（Anchor Break）：因 residual stale 觸發

6. Priority
   fill-driven rebuild > stale-driven rebuild

   Anchor Break 僅 override keep，不得覆蓋 fill-driven rebuild

7. Fill-Driven Rebuild
   當 PAIR_MODE 任一邊成交，必須 rebuild

   結果必須明確為：
   PAIR_MODE：BUY + SELL 均成功
   BUY_ONLY_MODE：BUY 成功，SELL 因 BTC 不足失敗
   SELL_ONLY_MODE：SELL 成功，BUY 因 USDC 不足失敗
   ABNORMAL：其他

   不得在結果未確定前進入 Anchor Break

8. Anchor Break Trigger
   僅在同時滿足時觸發：
   REANCHOR_BREAK = True
   mode 為 BUY_ONLY_MODE 或 SELL_ONLY_MODE
   僅 1 個掛單（Residual）
   Residual 滿足 stale
   不在 fill-driven rebuild 階段

9. Action
   cancel 所有掛單
   reference_price = 當前 mid grid
   rebuild pair

   不得修改價格
   不得部分修正

10. Keep
    PAIR_MODE：keep
    BUY_ONLY_MODE / SELL_ONLY_MODE 且 non-stale：keep

11. Constraints
    不得在 PAIR_MODE 或 ABNORMAL 下觸發 Anchor Break
    不得把資產不足等同 stale
    不得把單邊存在等同 stale
    必須先確定 mode，再允許 stale 判斷
    必須保持對稱與確定性

12. Logging
    日誌層允許分為兩類：
    contract branch 日誌：用於表達決策分支，例如 keep、anchor break、abnormal
    event-style 日誌：用於表達成交事件，例如 fill detected

    fill-driven rebuild 不強制要求固定字串 `contract: fill-driven rebuild`
    只要求日誌能明確表達：
    PAIR_MODE 下某一邊已成交
    rebuild 因 fill 觸發
    BUY filled 與 SELL filled 必須可區分

    keep 類日誌可做 rate limit
    anchor break、abnormal、cleanup failure、rebuild failure 不應被 keep rate limit 抑制

13. One Sentence
    Anchor Break = 在合法單邊模式下，當唯一殘單偏離過大時，用 mid grid 強制重建以替代 keep 的機制

如果你要，我下一条给你 **grid_logic.py + grid_runner_min.py 的 complete direct drop-in clean final version**。
