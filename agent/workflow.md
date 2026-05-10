````md
# Agent Workflow

## 目标

这个 agent 用于辅助 repo polish：

```text
任务 → 计划 → 审查 → 修订 → 接受 → 生成 patch → 检查 patch → 应用 patch → 验证 → 查看 diff
````

核心原则：

* LLM 只生成建议和 patch 意图。
* 人负责接受计划。
* 程序负责检查 patch 是否可安全应用。
* 不自动 commit。
* 不跳过验证。

## 文件角色

```text
agent/task.md
```

当前任务说明，必须写清楚目标、约束、允许修改的文件和成功标准。

```text
agent/final_plan.md
```

人工接受后的最终执行计划。

```text
agent/last_patch.txt
```

LLM 生成的 SEARCH/REPLACE patch blocks。

```text
agent/last_*.md
```

运行过程 trace，不是稳定资产。

## 标准流程

### 1. 生成计划

```bash
python agent/agent.py agent/task.md
```

### 2. 审查计划

```bash
python agent/agent.py agent/task.md --review-last
```

### 3. 修订计划

```bash
python agent/agent.py agent/task.md --revise-last
```

### 4. 查看最新计划

```bash
python agent/agent.py agent/task.md --show-last
```

### 5. 接受计划

```bash
python agent/agent.py agent/task.md --accept-last
```

生成：

```text
agent/final_plan.md
```

### 6. 检查是否可以进入 patch 阶段

```bash
python agent/agent.py agent/task.md --check-ready
```

必须看到：

```text
READY
```

### 7. 查看验证命令

```bash
python agent/agent.py agent/task.md --show-commands
```

### 8. 生成 patch

```bash
python agent/agent.py agent/task.md --make-patch
```

生成：

```text
agent/last_patch.txt
```

### 9. 检查 patch

```bash
python agent/agent.py agent/task.md --check-patch
```

必须看到：

```text
PATCH_OK
```

如果看到以下任一结果，停止：

```text
PATCH_INVALID
PATCH_NOT_SAFE
```

### 10. 应用 patch

```bash
python agent/agent.py agent/task.md --apply-patch
```

必须看到：

```text
PATCH_APPLIED
```

### 11. 运行验证

```bash
python agent/agent.py agent/task.md --run-verify
```

必须看到：

```text
VERIFY_OK
```

### 12. 查看真实 diff

```bash
git diff
```

确认：

* 只改了预期文件
* 没有无关重构
* 没有格式噪音
* 没有超出 `final_plan.md` 的行为变化

### 13. Commit

```bash
git status
git add <changed files>
git commit -m "<clear message>"
```

## 常用完整命令

```bash
python agent/agent.py agent/task.md
python agent/agent.py agent/task.md --review-last
python agent/agent.py agent/task.md --revise-last
python agent/agent.py agent/task.md --show-last
python agent/agent.py agent/task.md --accept-last
python agent/agent.py agent/task.md --check-ready
python agent/agent.py agent/task.md --make-patch
python agent/agent.py agent/task.md --check-patch
python agent/agent.py agent/task.md --apply-patch
python agent/agent.py agent/task.md --run-verify
git diff
```

## 简化流程

只适合很小、很熟悉的改动：

```bash
python agent/agent.py agent/task.md
python agent/agent.py agent/task.md --accept-last
python agent/agent.py agent/task.md --make-patch
python agent/agent.py agent/task.md --check-patch
python agent/agent.py agent/task.md --apply-patch
python agent/agent.py agent/task.md --run-verify
git diff
```

## 状态检查

查看 trace 状态：

```bash
python agent/agent.py agent/task.md --status
```

查看最终计划：

```bash
python agent/agent.py agent/task.md --show-final
```

清理运行 trace：

```bash
python agent/agent.py agent/task.md --clear-trace
```

`--clear-trace` 不会删除：

```text
agent/final_plan.md
```

## 停止规则

遇到以下情况立即停止：

* `--check-ready` 不是 `READY`
* `--check-patch` 不是 `PATCH_OK`
* `--run-verify` 不是 `VERIFY_OK`
* `git diff` 出现非预期文件
* patch 超出 `agent/task.md` 的 allowed files
* 实际改动超出 `agent/final_plan.md`

## 设计边界

这个 agent 可以：

* 生成计划
* 审查计划
* 修订计划
* 生成 patch block
* 检查 patch
* 应用已验证 patch
* 执行验证命令

这个 agent 不应该：

* 自动 commit
* 自动扩大修改范围
* 跳过人工接受计划
* 跳过 patch 检查
* 跳过验证
* 把 runtime trace 自动写入 POS 长期资产

```
```
