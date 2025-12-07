# ali_llm.py (Modified: Agentic Router)
#!/usr/bin/env python3
"""
llm_responder.py (Agentic RAG enabled)
...
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

from helper.utils_config import load_prompt_text
from helper.utils_llm import call_llm
from helper.utils_imap_types import EmailMessage

# NOTE: Update import path to the new filename
try:
    from helper_rag_worker import RagEngine
except ImportError:
    # 設置警告，RAG 模塊未加載時仍可運行
    RagEngine = None
    print("Warning: RagEngine could not be imported. RAG functionality disabled.")


# RAG 實例，首次使用時才會初始化 (Lazy Initialization)
_RAG_ENGINE: Optional[RagEngine] = None
_RAG_CLASSIFICATION_MODEL = "gpt-4o-mini" # 使用成本較低的模型進行分類


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
    Analyze the incoming email content and subject. Does the user ask about technical standards, 
    safety regulations (e.g., IEC, UL, EN, compliance), or technical certification?
    
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

def _get_rag_answer_lazy(question: str) -> str:
    """延遲加載並調用 RagEngine"""
    global _RAG_ENGINE
    if _RAG_ENGINE is None:
        if RagEngine is None:
             return ""
        # 首次调用时才初始化重型资源 (Lazy load)
        _RAG_ENGINE = RagEngine()

    try:
        # RagEngine.answer_question 返回 (answer, table_str)
        answer, _ = _RAG_ENGINE.answer_question(question)
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
    """
    對外主入口：
    ...
    """
    system_prompt = load_prompt_text(system_prompt_path.parent, system_prompt_path.name)
    if system_prompt is None:
        raise FileNotFoundError(f"Prompt file not found: {system_prompt_path}")
    
    subject = (email.subject or "").strip()
    body_text = (email.body_text or "").strip()

    # 2) Agentic Routing: 判斷是否需要安規知識
    rag_context = ""
    if _is_safety_regulation_query(subject, body_text):
        print(f"   [Router] Detected safety inquiry. Invoking RAG...")
        # 查詢整個郵件正文 (Question)
        rag_answer = _get_rag_answer_lazy(body_text)
        
        if rag_answer and "[RAG Error" not in rag_answer:
            # 將 RAG 答案包裝成一個內部參考，注入到 Prompt
            # LLM 會被指示使用這段內容來回答問題
            rag_context = (
                f"\n\n--- INTERNAL REFERENCE (DO NOT SHOW TO USER) ---\n"
                f"Use this information to answer the user's inquiry about safety standards/regulations:\n"
                f"{rag_answer}\n"
                f"---------------------------------------------------\n"
            )
            print(f"   [Agent] RAG context successfully injected.")
        else:
             print(f"   [Agent] RAG detected, but no useful answer found or error occurred.")


    # 3) 把主旨、正文和 RAG Context 組合成 user_text
    parts: list[str] = []
    
    if subject:
        parts.append(f"Subject: {subject}")
    
    if body_text:
        parts.append(body_text)
    
    if rag_context:
        # RAG Context 放在 User Text 的結尾，用於指導 LLM 回信
        parts.append(rag_context)
        
    user_text = "\n\n".join(parts)

    # 4) 呼叫 LLM
    reply_body = call_llm(
        model=model,
        system_prompt=system_prompt,
        user_text=user_text,
        file_path=None,
    )

    return reply_body.strip()