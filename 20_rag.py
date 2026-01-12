# 20_rag.py (Modified: Thin Wrapper for Demo)
#!/usr/bin/env python3
"""
20_rag.py (Demo Wrapper)

Wraps helper_rag_worker.RagEngine for CLI demonstration and original function signature.
All core logic now resides in helper_rag_worker.py.
"""
from __future__ import annotations

from pathlib import Path
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
    # 保留原有的 CLI 運行邏輯 (Preserve original CLI run logic)
    q = Path("prompt/prompt_rag_user.txt").read_text(encoding="utf-8")
    print(f"Query: {q.strip()[:60]}...")
    
    # 調用新的包裝函數
    answer, sources = answer_standard_question(q)
    
    print("\n=== Answer ===\n")
    print(answer)
