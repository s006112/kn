# ali_llm.py (Modified: Agentic Router)
#!/usr/bin/env python3
"""
llm_responder.py (Agentic RAG enabled)
...
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from helper.utils_config import load_prompt_text
from helper.utils_llm import call_llm
from helper.utils_imap_types import EmailMessage

# NOTE: Update import path to the new filename
if TYPE_CHECKING:
    from helper.helper_rag_worker import RagEngine as RagEngineType

try:
    from helper.helper_rag_worker import RagEngine
except ImportError:
    # 設置警告，RAG 模塊未加載時仍可運行
    RagEngine = None
    print("Warning: RagEngine could not be imported. RAG functionality disabled.")


# RAG 實例，首次使用時才會初始化 (Lazy Initialization)
_RAG_ENGINE: Optional["RagEngineType"] = None
_RAG_CLASSIFICATION_MODEL = "gpt-4.1-mini" # 使用成本較低的模型進行分類


# -----------------------------------------------------------------------------
# 1. Router Logic (判斷是否需要 RAG)
# -----------------------------------------------------------------------------

def _is_safety_regulation_query(subject: str, body: str) -> bool:
    """
    使用 LLM 快速判斷這封郵件是否詢問安規/標準相關問題。
    """
    if RagEngine is None:
        return False # RAG 模塊未加載，直接跳過

    check_prompt = """
    You are a classification agent.
    Analyze the incoming email content and subject to justify whether the content mentioned about technically related standards, 
    safety regulations (e.g., IEC, UL, EN, compliance), or certification?
    
    Respond with exactly one word: YES or NO.
    """
    
    content = f"Subject: {subject}\n\n{body}"[:1500] 
    
    try:
        # 使用快速模型進行意圖分類
        resp = call_llm(
            model=_RAG_CLASSIFICATION_MODEL, 
            system_prompt=check_prompt, 
            user_text=content,
            max_retries=1
        )
        return "YES" in resp.strip().upper()
    except Exception as e:
        print(f"Classification failed, falling back to general reply: {e}")
        return False

# -----------------------------------------------------------------------------
# 2. RAG Execution & Retrieval
# -----------------------------------------------------------------------------

def _load_rag_engine() -> Optional["RagEngineType"]:
    """延遲加載 RagEngine (Lazy load)."""
    global _RAG_ENGINE
    if _RAG_ENGINE is not None:
        return _RAG_ENGINE
    if RagEngine is None:
        return None
    _RAG_ENGINE = RagEngine()
    return _RAG_ENGINE


def _get_rag_answer_lazy(question: str) -> str:
    """延遲加載並調用 RagEngine"""
    rag_engine = _load_rag_engine()
    if rag_engine is None:
        return ""

    try:
        # RagEngine.answer_question 返回 (answer, table_str)
        answer, table_str = rag_engine.answer_question(question)
        if table_str:
            print("\n[RAG] FAISS similarity table:\n")
            print(table_str)
            print()
        return answer
    except Exception as e:
        print(f"RAG Retrieval or Generation failed: {e}")
        return f"[RAG Error: Unable to retrieve technical answer. Use general knowledge.]"


# -----------------------------------------------------------------------------
# 3. Main Generation Logic
# -----------------------------------------------------------------------------

def generate_reply(
    email: EmailMessage,
    *,
    system_prompt_path: Path,
    model: str,
) -> str:
    """對外主入口：根據 email 內容產生回覆。"""
    system_prompt = load_prompt_text(system_prompt_path.parent, system_prompt_path.name)
    if system_prompt is None:
        raise FileNotFoundError(f"Prompt file not found: {system_prompt_path}")
    
    subject = (email.subject or "").strip()
    body_text = (email.body_text or "").strip()

    # 1) Agentic Routing: 判斷是否需要安規知識
    if _is_safety_regulation_query(subject, body_text):
        print(f"   [Router] Detected safety inquiry. Invoking RAG...")
        rag_answer = _get_rag_answer_lazy(body_text)
        # 若 RAG 成功給出專業回答，直接作為回信內容返回
        if rag_answer and "[RAG Error" not in rag_answer:
            print(f"   [Agent] RAG answer generated. Using it as reply body.")
            return rag_answer.strip()
        print(f"   [Agent] RAG detected, but no useful answer found or error occurred. Falling back to general model.")

    # 2) 非 RAG 或 RAG 失敗：使用一般模型，以主旨與正文產生回覆
    parts: list[str] = []
    
    if subject:
        parts.append(f"Subject: {subject}")
    
    if body_text:
        parts.append(body_text)
        
    user_text = "\n\n".join(parts)

    # 3) 呼叫一般 LLM
    reply_body = call_llm(
        model=model,
        system_prompt=system_prompt,
        user_text=user_text,
        file_path=None,
    )

    return reply_body.strip()
