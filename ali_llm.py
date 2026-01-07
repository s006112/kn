#!/usr/bin/env python3
"""
ali_llm.py

- Internal review generation pipeline
- Step1 routing + Step2 retrieval
- Step3 draft generation (v1 rewrite or v2+ edit-only)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from helper.utils_config import load_prompt_text
from helper.utils_llm import call_llm
from helper.utils_imap_types import EmailMessage
from ali_router import RouteResult, route_email
from ali_mail_parse import (
    REVIEW_FOOTER_LINE,
    REVIEW_HEADER_LINE_TEMPLATE,
    extract_override_instructions,
    normalize_email_input,
)

# -----------------------------------------------------------------------------
# RAG imports 
# -----------------------------------------------------------------------------
if TYPE_CHECKING:
    from helper.helper_rag_worker import RagEngine as RagEngineType

try:
    from helper.helper_rag_worker import RagEngine
except ImportError:
    RagEngine = None
    print("Warning: RagEngine could not be imported. RAG functionality disabled.")

_RAG_ENGINE: Optional["RagEngineType"] = None


@dataclass(frozen=True)
class RetrievalResult:
    used: bool
    context: str | None
    source: str | None


def step2_retrieval(route: "RouteResult", subject: str, body: str) -> RetrievalResult:
    if route.category != "safety_regulation":
        return RetrievalResult(used=False, context=None, source=None)

    rag_answer = _get_rag_answer_lazy(body)
    if rag_answer:
        return RetrievalResult(used=True, context=rag_answer, source="rag")
    return RetrievalResult(used=False, context=None, source=None)

# -----------------------------------------------------------------------------
# RAG Execution 
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
# Internal Review Package (v1 rewrite, v2+ edit-only)
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
    subject_norm, body_norm = normalize_email_input(email)

    if previous_draft is None:
        # v1 — rewrite
        route = route_email(subject_norm, body_norm)
        retrieval = step2_retrieval(route, subject_norm, body_norm)

        if retrieval.used:
            draft = retrieval.context.strip()
        else:
            system_prompt = load_prompt_text(
                system_prompt_path.parent, system_prompt_path.name
            )
            if system_prompt is None:
                raise FileNotFoundError(
                    f"Prompt file not found: {system_prompt_path}"
                )
            parts: list[str] = []
            if subject_norm:
                parts.append(f"Subject: {subject_norm}")
            if body_norm:
                parts.append(body_norm)
            user_text = "\n\n".join(parts)
            draft = call_llm(
                model=model,
                system_prompt=system_prompt,
                user_text=user_text,
                file_path=None,
            ).strip()
    else:
        # v2+ — edit-only (NO rewrite fallback)
        # NOTE:
        # Routing and retrieval are intentionally bypassed for v2+ edit-only path.
        # This is a hard invariant.
        edit_prompt_path = (
            system_prompt_path.parent / "prompt_edit_only_override.txt"
        )
        edit_system_prompt = load_prompt_text(
            edit_prompt_path.parent, edit_prompt_path.name
        )
        if edit_system_prompt is None:
            raise FileNotFoundError(
                f"Edit-only prompt not found: {edit_prompt_path}"
            )

        override_text = extract_override_instructions(body_norm)
        user_text = (
            "previous_draft:\n"
            f"{previous_draft}\n\n"
            "---\n"
            "override_instructions:\n"
            f"{override_text}"
        )

        draft = call_llm(
            model=model,
            system_prompt=edit_system_prompt,
            user_text=user_text,
            file_path=None,
        ).strip()

    # -------------------------
    # Assemble review body
    # -------------------------

    review_body = f"""
{REVIEW_HEADER_LINE_TEMPLATE.format(version=edit_version)}

{draft}

{REVIEW_FOOTER_LINE}
""".strip()

    review_id = (email.message_id or "").strip() or str(email.uid)

    return {
        "review_id": review_id,
        "draft": review_body,
        "allowed_actions": ["OVERRIDE", "REJECT"],
    }


def render_review(review_obj: dict[str, str | list[str]]) -> str:
    """
    Render the review package into the final email body.

    Today this is intentionally a thin wrapper that returns `review_obj["draft"]`.
    Keeping a dedicated function gives us a single place to evolve formatting later
    (e.g., include `review_id`, allowed actions, extra headers/sections) without
    changing call sites such as `ali_email.py`.
    """
    return review_obj["draft"]
