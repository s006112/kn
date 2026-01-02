#!/usr/bin/env python3
"""
ali_llm.py

- Preserve existing Agentic Router + Lazy RAG behavior
- Support INTERNAL review generation (v1 rewrite)
- Support EDIT-ONLY override workflow (v2, v3, ...)
- No rewrite fallback in override path
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
# 3. Original generation logic (PRESERVED for v1)
# -----------------------------------------------------------------------------

def generate_reply(
    email: EmailMessage,
    *,
    system_prompt_path: Path,
    model: str,
) -> str:
    """
    v1 behavior:
    Generate a raw reply body based on email content.
    (This output is NOT customer-approved.)
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
# 4. Internal Review Package (v1 rewrite, v2+ edit-only)
# -----------------------------------------------------------------------------

def generate_review_package(
    email: EmailMessage,
    *,
    system_prompt_path: Path,
    model: str,
    previous_draft: str | None = None,
    edit_version: int = 1,
) -> dict[str, str | list[str]]:
    """
    INTERNAL review generator.

    Rules:
    - v1  : rewrite using generate_reply()
    - v2+ : EDIT ONLY previous_draft using edit-only prompt
    - Any reply implies OVERRIDE; silence implies REJECT
    """

    # -------------------------
    # Draft generation
    # -------------------------

    if previous_draft is None:
        # v1 — rewrite
        draft = generate_reply(
            email,
            system_prompt_path=system_prompt_path,
            model=model,
        )
    else:
        # v2+ — edit-only (NO rewrite fallback)
        edit_prompt_path = (
            system_prompt_path.parent / "prompt_edit_only_override_p.txt"
        )
        edit_system_prompt = load_prompt_text(
            edit_prompt_path.parent, edit_prompt_path.name
        )
        if edit_system_prompt is None:
            raise FileNotFoundError(
                f"Edit-only prompt not found: {edit_prompt_path}"
            )

        user_text = (
            "previous_draft:\n"
            "----------------\n"
            f"{previous_draft}\n\n"
            "override_instructions:\n"
            "----------------------\n"
            f"{(email.body_text or '').strip()}"
        )

        draft = call_llm(
            model=model,
            system_prompt=edit_system_prompt,
            user_text=user_text,
            file_path=None,
        ).strip()

    # -------------------------
    # Subject normalization
    # -------------------------

    original_subject = (email.subject or "").strip()
    normalized_subject = f"[ALI REVIEW] {original_subject}" if original_subject else "[ALI REVIEW]"

    # -------------------------
    # Reflection notes (static for now)
    # -------------------------

    reflection_notes = [
        "Potential over-commitment risk exists.",
        "Verify no implicit compliance or certification claims.",
        "Additional application context may be required.",
    ]

    # -------------------------
    # Assemble review body
    # -------------------------

    review_body = f"""
[ALI INTERNAL REVIEW — NOT FOR CUSTOMER]
EDIT VERSION: v{edit_version}

Original Subject:
{normalized_subject}

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
SENDER ACTION REQUIRED
==================================================
If you reply, your entire reply body is treated as EDIT instructions
applied strictly to the previous draft.

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
