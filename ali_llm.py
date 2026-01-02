# ali_llm.py (Modified: Agentic Router + Internal Review Package)
#!/usr/bin/env python3
"""
ali_llm.py

- Preserve existing Agentic Router + Lazy RAG behavior
- Add INTERNAL review package generation for engineer-only workflow
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from helper.utils_config import load_prompt_text
from helper.utils_llm import call_llm
from helper.utils_imap_types import EmailMessage

# -----------------------------------------------------------------------------
# RAG imports (unchanged)
# -----------------------------------------------------------------------------
if TYPE_CHECKING:
    from helper.helper_rag_worker import RagEngine as RagEngineType

try:
    from helper.helper_rag_worker import RagEngine
except ImportError:
    RagEngine = None
    print("Warning: RagEngine could not be imported. RAG functionality disabled.")

_RAG_ENGINE: Optional["RagEngineType"] = None
_RAG_CLASSIFICATION_MODEL = "gpt-4.1-mini"


# -----------------------------------------------------------------------------
# 1. Router Logic (unchanged)
# -----------------------------------------------------------------------------

def _is_safety_regulation_query(subject: str, body: str) -> bool:
    if RagEngine is None:
        return False

    check_prompt = """
    You are a classification agent.
    Analyze the incoming email content and subject to determine whether it
    involves technical standards, safety regulations (IEC, UL, EN),
    compliance, or certification topics.

    Respond with exactly one word: YES or NO.
    """

    content = f"Subject: {subject}\n\n{body}"[:1500]

    try:
        resp = call_llm(
            model=_RAG_CLASSIFICATION_MODEL,
            system_prompt=check_prompt,
            user_text=content,
            max_retries=1,
        )
        return "YES" in resp.strip().upper()
    except Exception as e:
        print(f"Classification failed, falling back to general reply: {e}")
        return False


# -----------------------------------------------------------------------------
# 2. RAG Execution (unchanged)
# -----------------------------------------------------------------------------

def _load_rag_engine() -> Optional["RagEngineType"]:
    global _RAG_ENGINE
    if _RAG_ENGINE is not None:
        return _RAG_ENGINE
    if RagEngine is None:
        return None
    _RAG_ENGINE = RagEngine()
    return _RAG_ENGINE


def _get_rag_answer_lazy(question: str) -> str:
    rag_engine = _load_rag_engine()
    if rag_engine is None:
        return ""

    try:
        answer, table_str = rag_engine.answer_question(question)
        if table_str:
            print("\n[RAG] FAISS similarity table:\n")
            print(table_str)
            print()
        return answer
    except Exception as e:
        print(f"RAG Retrieval or Generation failed: {e}")
        return ""


# -----------------------------------------------------------------------------
# 3. Original generation logic (PRESERVED)
# -----------------------------------------------------------------------------

def generate_reply(
    email: EmailMessage,
    *,
    system_prompt_path: Path,
    model: str,
) -> str:
    """
    Original behavior:
    Generate a raw reply body based on email content.
    (This output is NOT considered customer-approved.)
    """
    system_prompt = load_prompt_text(system_prompt_path.parent, system_prompt_path.name)
    if system_prompt is None:
        raise FileNotFoundError(f"Prompt file not found: {system_prompt_path}")

    subject = (email.subject or "").strip()
    body_text = (email.body_text or "").strip()

    # Agentic routing
    if _is_safety_regulation_query(subject, body_text):
        print("   [Router] Detected safety inquiry. Invoking RAG...")
        rag_answer = _get_rag_answer_lazy(body_text)
        if rag_answer:
            print("   [Agent] RAG answer generated.")
            return rag_answer.strip()
        print("   [Agent] RAG failed. Falling back to general model.")

    parts: list[str] = []
    if subject:
        parts.append(f"Subject: {subject}")
    if body_text:
        parts.append(body_text)

    user_text = "\n\n".join(parts)

    reply_body = call_llm(
        model=model,
        system_prompt=system_prompt,
        user_text=user_text,
        file_path=None,
    )

    return reply_body.strip()


# -----------------------------------------------------------------------------
# 4. NEW: Internal Review Package (Minimal Advanced Step)
# -----------------------------------------------------------------------------

def generate_review_package(
    email: EmailMessage,
    *,
    system_prompt_path: Path,
    model: str,
) -> dict[str, str | list[str]]:
    """
    Generate an INTERNAL review package for engineer only.

    - Reuses existing generate_reply()
    - Adds minimal reflection + explicit human gate
    - ANY reply implies OVERRIDE instructions; silence means REJECT
    - NEVER represents a customer-approved response
    """

    # Step 1: get preliminary draft (existing capability)
    draft = generate_reply(
        email,
        system_prompt_path=system_prompt_path,
        model=model,
    )

    subject = (email.subject or "").strip()

    # Step 2: minimal reflection (deliberately simple for step 1)
    reflection_notes = [
        "Potential over-commitment risk exists.",
        "Verify no implicit compliance or certification claims.",
        "Additional application context may be required.",
    ]

    # Step 3: assemble engineer-facing review package
    review_body = f"""
[ALI INTERNAL REVIEW — NOT FOR CUSTOMER]

Original Subject:
{subject}

==================================================
PRELIMINARY DRAFT (NOT APPROVED)
==================================================
{draft}

==================================================
REFLECTION / RISK NOTES
==================================================
- {reflection_notes[0]}
- {reflection_notes[1]}
- {reflection_notes[2]}

==================================================
ENGINEER ACTION REQUIRED
==================================================
If you reply, your entire reply body is treated as OVERRIDE instructions.
If you do not reply, this is treated as REJECT and will not proceed.

(Do NOT forward this draft to customer without manual approval.)
""".strip()

    review_id = (email.message_id or "").strip() or str(email.uid)

    return {
        "review_id": review_id,
        "draft": review_body,
        "allowed_actions": ["OVERRIDE", "REJECT"],
    }


def render_review(review_obj: dict[str, str | list[str]]) -> str:
    return review_obj["draft"]
