# 20_rag.py (Modified: Thin Wrapper for Demo)
#!/usr/bin/env python3
"""
20_rag.py (Demo Wrapper)

Wraps helper_rag_worker.RagEngine for CLI demonstration and original function signature.
All core logic now resides in helper_rag_worker.py.
"""
from __future__ import annotations

from typing import Tuple

# 引入核心 RAG 邏輯
from helper.helper_rag_worker import RagEngine

# 保持原有的對外函數簽名 (Preserve original function signature)
def answer_standard_question(question: str) -> Tuple[str, str]:
    """
    Finds the answer to a standard/regulation question using RAG.
    NOTE: This is a wrapper for RagEngine.answer_question.
    """
    engine = RagEngine()
    # 調用封裝的 RagEngine 邏輯
    answer, sources = engine.answer_question(question)
    
    # 保留原始的 CLI 輸出格式 (Print table for demo)
    print("\n=== Top hits ===\n")
    print(sources, flush=True) 
    
    return answer, sources


if __name__ == "__main__":
    q = """This is a question about UL 935 Fluorescent-Lamp Ballasts（荧光灯镇流器）

对于内置于灯具或单独安装的荧光灯镇流器，UL 935 如何要求其在异常工况（如灯管开路、短路或寿命终止）下的温升和绝缘耐压？

* 试验时的供电条件和持续时间有哪些关键要求？
* 合格判定里，温升、绝缘失效或冒烟起火的判据是什么？ """
    answer, sources = answer_standard_question(q)
    
    print("\n=== Answer ===\n")
    print(answer)
