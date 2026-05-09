## 1. Core judgment

`is_file_ready()` 不应该继续放在 `w/p_ttml.py`，也不应该抽成 generic helper。

最小且更合适的归属是：**移动到 `w/p_pipelines.py`，作为 TTML pipeline 局部私有 helper，例如 `_is_file_ready()`。**

理由：

- `w/p_ttml.py` 的实际职责是 **TTML / plain subtitle 转换与归档**，`is_file_ready()` 做的是文件大小稳定性检查，不属于格式转换逻辑。
- readiness check 发生在 `process_ttml_pipeline()` 中，属于运行时 intake / worker 处理前的安全检查。
- 但它也不等同于 queue scanning 或 file locking：
  - scanner 只负责发现并入队 `.ttml`
  - readiness 负责判断文件是否还在写入
  - lock 负责避免并发处理
  - conversion 交给 `handle_ttml()`
- 当前允许文件中只有一个真实调用点：`process_ttml_pipeline()`。
- `w/p_audio.py` 没有复用相同 readiness 逻辑。
- 因此不满足“至少两个真实调用点”这一 shared helper 提取条件。

结论：**放进 `w/p_pipelines.py`，靠近 `process_ttml_pipeline()`，保持私有、局部、可逆。**

---

## 2. Minimal patch plan

1. 修改 `w/p_pipelines.py`
   - 将 import 从：
     - `from .p_ttml import handle_ttml, is_file_ready`
   - 改为只导入：
     - `handle_ttml`
   - 在 `process_ttml_pipeline()` 附近新增私有函数：
     - `_is_file_ready(path, wait=1.0)`
   - 函数体保持现有行为不变：
     - 第一次 `os.path.getsize(path)`
     - `time.sleep(wait)`
     - 第二次 `os.path.getsize(path)`
     - 比较两次 size 是否一致
   - 将调用点：
     - `is_file_ready(src, wait=wait_seconds)`
   - 改为：
     - `_is_file_ready(src, wait=wait_seconds)`

2. 修改 `w/p_ttml.py`
   - 删除 `is_file_ready()` 函数。
   - 删除不再使用的 `import time`。
   - 不修改 `handle_ttml()`、`extract_text()`、`process_text()` 行为。

3. 不修改 `w/p_audio.py`
   - 没有真实复用需求。

4. 不修改 `w/evaluation.py`
   - 当前 evaluation 只从 `w.p_ttml` 导入 `handle_ttml`，不需要调整。

---

## 3. Files to touch

- `w/p_pipelines.py`
- `w/p_ttml.py`

不新增文件。  
不新增 generic helper。  
不修改 `w/p_audio.py` / `w/evaluation.py`。

---

## 4. Risks / boundary conditions

- 风险：如果允许范围外存在代码直接导入 `w.p_ttml.is_file_ready`，删除该 symbol 会破坏外部调用。当前允许文件中没有这种调用，只能作为边界风险记录。
- 行为边界：
  - 不改变等待时间来源，仍使用 `INTERVALS["WAIT_SECONDS"]`。
  - 不改变文件消失时的异常路径；`getsize()` 抛错仍由 `process_ttml_pipeline()` 外层异常处理接住。
  - 不把 readiness check 放进 `handle_ttml()`，避免转换函数承担 runtime orchestration 职责。
  - 不把 readiness check 放进 scanner，避免扫描阶段阻塞或混入处理前状态判断。
  - 不抽到 shared helper，避免单调用点 premature abstraction。

---

## 5. Evaluation command

建议执行：

```bash
python w/evaluation.py
```

可选快速语法检查：

```bash
python -m py_compile w/p_pipelines.py w/p_ttml.py
```

---

## 6. Stop condition

停止条件：

- `w/p_ttml.py` 不再包含 `is_file_ready()` 和未使用的 `time` import。
- `w/p_pipelines.py` 内部拥有私有 `_is_file_ready()`。
- `process_ttml_pipeline()` 使用该私有 helper。
- `handle_ttml()` 转换行为未改。
- 没有新增 helper 文件。
- evaluation 通过。