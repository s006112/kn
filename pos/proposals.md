# Proposals（候選規則）

從真實工作中提取、但尚未穩定的候選規則。

## 提升規則

- 只有在重複驗證並獲得明確人工批准後，才提升 proposal。
- 若 proposal 與 stable assets 變得重複，應 merge 或 delete。

## Proposal 001 - 優先使用 explicit dependency passing，而不是 hidden global state

**模式**

- Hidden global state 會讓 runtime ownership 變得不清楚。

**建議規則**

- 優先使用 explicit dependency passing，而不是 module-level mutable state。

**條件**

- Function 可以直接接收所需 object。
- Runtime state 已存在於可見 scope 中。
- Global 只為方便而存在。

**邊界**

- 對真正的 singleton process interfaces 或 compatibility shims，可接受 temporary global state。
- 如果移除 global state 會破壞 public interface，應有意識地保留 compatibility，或明確記錄 breaking change。

**存放位置**

- `assets.md` / Code Iteration Principles

**狀態**

- pending

## Proposal 002 - 只有在能釐清 ownership 時才保留 lightweight semantic bundles

**模式**

- 不是每個 class-like 或 grouped structure 都是 over-engineering，但很多會變成模糊的 state containers。

**建議規則**

- 只有當 lightweight bundles 比傳遞鬆散 raw values 更能釐清 runtime ownership 時，才保留它們。

**條件**

- Bundle 聚合相關 runtime handles。
- Field names 改善可讀性。
- 它避免脆弱的 tuple ordering。
- 它不隱藏 execution flow。

**邊界**

- 不要把 data bundle 轉成 behavior-heavy class。
- 當 concrete names 更清楚時，不要使用 generic context objects 等模糊名稱。
- 不要用 hidden bag of state 取代可讀的 function signature。

**存放位置**

- `assets.md` / Code Iteration Principles

**狀態**

- pending

## Proposal 003 - Intake 應遵守 route enablement

**模式**

- 忽略 pipeline toggles 的 scanner 會製造隱藏工作與不清楚的 system state。

**建議規則**

- Intake 應遵守 route enablement。

**條件**

- Disabled worker 通常意味著其 intake route 應停止 enqueue 新工作。
- 除非有明確意圖，scanner 不應為 disabled routes 建立 backlog。
- Toggle behavior 應符合 operator 的 mental model。

**邊界**

- 可以有意識地引入 dedicated backlog-building mode。
- 若某些 runtime services 的目的獨立於 route processing，它們可以保持 always-on。
- 此規則適用於 intake routes，不一定適用於所有 background services。

**存放位置**

- `assets.md` / Pipeline Concepts

**狀態**

- pending

## Proposal 004 - 當能減少 branches 時，按 stage 集中 error behavior

**模式**

- Per-model 與 per-stage error handling 可能蔓延成重複的 local branches。

**建議規則**

- 當 stage-level semantics 相同時，集中 error save/log behavior。

**條件**

- Error marker path format 共享。
- Log format 共享。
- Failure routing 共享。
- Centralization 能移除重複 local code，且不隱藏不同行為。

**邊界**

- 不要集中需要不同 recovery semantics 的 errors。
- 不要把 destructive moves 或 retry decisions 隱藏在 generic helper 中。
- 不要只為了 silent continue 而 catch exceptions。

**存放位置**

- `assets.md` / Code Iteration Principles

**狀態**

- pending

## Proposal 005 - 優先使用具有 operational meaning 的 route names

**模式**

- `full`、`light` 這類模糊標籤，或 generic context names，會造成反覆澄清。

**建議規則**

- 使用能直接描述 operational meaning 的 route names 與 variable names。

**條件**

- 名稱說明接下來會發生什麼。
- 名稱符合 operator 的 mental model。
- 當真正差異是 route、policy 或 output type 時，名稱避免使用模糊的強度詞。

**邊界**

- 不要隨意重新命名 stable public APIs。
- 不要只因個人品味重新命名。
- 只有在能減少未來誤解時才重新命名。

**存放位置**

- `assets.md` / Naming Principles

**狀態**

- pending
