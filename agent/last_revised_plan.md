## 1. Core judgment

`is_file_ready()` 不应抽成通用 helper；在当前边界下，最小且清晰的归属是放在 `w/p_pipelines.py`，作为 TTML pipeline 的局部私有函数。

判断依据：

- `w/p_ttml.py` 的主职责是 TTML / plain subtitle 的转换与处理，不应承载文件大小稳定性判断。
- `is_file_ready()` 是处理前的运行时准备检查，属于 pipeline orchestration 的一部分，而不是 TTML 格式转换逻辑。
- 该检查与队列扫描、文件锁定、以及 `handle_ttml()` 的转换职责不同：
  - scanner 负责发现并入队
  - readiness 负责确认文件已稳定
  - lock 负责并发控制
  - conversion 负责内容处理
- 当前只有一个真实调用点，不能抽成共享 helper。

结论：**将 `is_file_ready()` 迁入 `w/p_pipelines.py`，以私有 helper 形式保留在 `process_ttml_pipeline()` 附近。**

---

## 2. Minimal patch plan

1. 修改 `w/p_pipelines.py`
   - 去掉对 `w.p_ttml.is_file_ready` 的导入。
   - 在 `process_ttml_pipeline()` 附近新增私有函数 `_is_file_ready(path, wait=1.0)`。
   - 保持现有逻辑不变：
     - 第一次 `os.path.getsize(path)`
     - `time.sleep(wait)`
     - 第二次 `os.path.getsize(path)`
     - 比较两次 size 是否一致
   - 将现有调用点切换为 `_is_file_ready(src, wait=wait_seconds)`。

2. 修改 `w/p_ttml.py`
   - 删除 `is_file_ready()`。
   - 删除不再使用的 `time` import。
   - 不改动 `handle_ttml()` 及其转换流程。

3. 不修改 `w/p_audio.py`
   - 没有真实复用需求。

4. 不修改 `w/evaluation.py`
   - 现有验证路径不依赖该函数的公开导出。

---

## 3. Files to touch

- `w/p_pipelines.py`
- `w/p_ttml.py`

不新增文件。  
不新增共享 helper。  
不修改 `w/p_audio.py` / `w/evaluation.py`。

---

## 4. Risks / boundary conditions

- 边界风险：如果允许范围外存在外部代码直接导入 `w.p_ttml.is_file_ready`，删除该符号会影响外部调用；当前任务范围内未见此依赖。
- 行为边界：
  - 不改变等待时长来源，仍使用现有 `wait_seconds` / `INTERVALS["WAIT_SECONDS"]`。
  - 不改变 `getsize()` 异常的传播与外层处理方式。
  - 不把 readiness 检查放进 `handle_ttml()`，避免把 orchestration 混入转换职责。
  - 不抽到共享 helper，避免单调用点过度抽象。
- 归属边界：
  - 该 helper 仅服务 TTML pipeline，不扩展到其他 pipeline。
  - 以私有函数形式放在 `w/p_pipelines.py`，只覆盖当前调用链。

---

## 5. Evaluation command

建议执行：

```bash
python -m py_compile w/p_pipelines.py w/p_ttml.py
python w/evaluation.py
```

---

## 6. Stop condition

停止条件：

- `w/p_ttml.py` 不再导出 `is_file_ready()`。
- `w/p_pipelines.py` 内部使用私有 `_is_file_ready()`。
- `process_ttml_pipeline()` 的 readiness 行为未变。
- `handle_ttml()` 的转换行为未变。
- 未引入共享 helper。
- 语法检查与 evaluation 通过。

---

## 7. Revision note

补强了归属判断与验证步骤：保留“不抽 shared helper、行为不变”的边界，同时明确 readiness 属于 pipeline 侧的处理前检查，并用更直接的语法检查配合 evaluation 确认未引入行为变化。