# Final Accepted Plan

Source: `/workspaces/kn/agent/last_revised_plan.md`

---

1. **Core judgment**

需要修订。  
原计划的方向基本正确：**不抽 shared helper，保持行为不变**。  
但归属判断要更谨慎地表述为：**`is_file_ready()` 最小改动下应移到 `w/p_pipelines.py`，并保持为 TTML pipeline 的私有实现细节**，因为它属于 pipeline 运行前的就绪检查，不属于 `w/p_ttml.py` 的格式转换职责。

2. **Revised minimal patch plan**

1. **确认归属边界**
   - `w/p_ttml.py` 继续只负责 TTML / 文本处理相关逻辑。
   - `is_file_ready()` 不保留在 `w/p_ttml.py`，因为它不是转换逻辑。
   - 不抽成 generic helper，因为当前只有一个真实调用点。

2. **将就绪检查下移到 pipeline 侧**
   - 在 `w/p_pipelines.py` 中，把文件稳定性检查作为 TTML pipeline 的私有辅助逻辑保留在 `process_ttml_pipeline()` 附近。
   - 该逻辑仅服务于 pipeline orchestration 中的“是否可以交给 TTML 处理”的判断，不进入 `handle_ttml()`。

3. **保持现有行为不变**
   - 仍然使用相同的 size-stability 判断方式。
   - 仍然保留原有等待时间来源与异常传播路径。
   - 不改变 queue 扫描、文件锁、或 TTML 转换本身的职责划分。

4. **清理旧位置**
   - 从 `w/p_ttml.py` 移除 `is_file_ready()` 及其不再需要的导入。
   - `w/p_pipelines.py` 调整为直接使用本地私有实现。
   - 不触及 `w/p_audio.py`、`w/evaluation.py` 的业务逻辑。

5. **验证**
   - 先做最直接的语法/导入检查，确认重定位后无引用断裂。
   - 再运行现有 evaluation，确认行为没有变化。

3. **Files to touch**

- `w/p_pipelines.py`
- `w/p_ttml.py`

4. **Risks / boundary conditions**

- 这次变更只应影响 ownership，不应改变 readiness 的判断规则。
- 不能把就绪检查并入 `handle_ttml()`，否则会混入转换职责。
- 不能抽成共享 helper，除非后续出现至少两个真实调用点。
- 若仓库外部存在对 `w.p_ttml.is_file_ready` 的直接导入，那会是边界风险；但在本次允许范围内不扩展处理。

5. **Evaluation command**

优先做直接检查：

```bash
python -m py_compile w/p_pipelines.py w/p_ttml.py
```

再运行：

```bash
python w/evaluation.py
```

6. **Stop condition**

- `is_file_ready()` 不再属于 `w/p_ttml.py` 的职责。
- 其实现只作为 `w/p_pipelines.py` 中 TTML pipeline 的局部私有逻辑存在。
- 行为、等待、异常路径保持不变。
- 没有新增 shared helper，没有扩大到其他文件。

7. **Revision note**

相较于上一版，唯一实质变化是：  
- **把“归属决定”说得更保守、更清楚**：不是为了重构而移动，而是为了让 readiness 明确归入 pipeline orchestration，同时与 TTML 转换职责分离。  
- **把验证步骤前移并具体化**：先做语法/导入检查，再跑 evaluation，以确认只是 ownership 调整，没有行为变化。