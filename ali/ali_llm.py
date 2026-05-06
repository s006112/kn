#!/usr/bin/env python3
"""
ali_llm.py

- Internal review generation pipeline
- Step1 routing + Step2 retrieval
- Step3 draft generation (v1 rewrite or v2+ edit-only)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helper.helper_config import load_prompt_text
from helper.helper_llm import call_llm
from helper.utils_imap_types import EmailMessage
from rag.helper_rag_pipeline import get_rag_engine
from ali.ali_router import RouteResult, route_email
from ali.ali_mail_parse import (
    REVIEW_FOOTER_LINE,
    REVIEW_HEADER_LINE_TEMPLATE,
    extract_override_instructions,
    normalize_email_input,
)


@dataclass(frozen=True)
class RetrievalResult:
    used: bool
    context: str | None
    source: str | None


RAG_ENGINE_BY_CATEGORY = {
    "safety_regulation": "standard",
    "technical": "standard",
    "rita": "rita",
}
_RAG_ENGINE_CACHE = {}


def rag_retrieval(route: "RouteResult", subject: str, body: str) -> RetrievalResult:
    engine_name = RAG_ENGINE_BY_CATEGORY.get(route.category)
    if engine_name is None:
        return RetrievalResult(used=False, context=None, source=None)

    try:
        if engine_name not in _RAG_ENGINE_CACHE:
            _RAG_ENGINE_CACHE[engine_name] = get_rag_engine(engine_name)

        query_parts = []
        if subject:
            query_parts.append(f"Subject: {subject}")
        if body:
            query_parts.append(body)

        query = "\n\n".join(query_parts).strip()

        if route.category == "rita":
            query = (
                f"{query}\n\n"
                "Search intent: find relevant historical records from the selected source collection. "
                "Prioritize matching context, participants, dates, content, attachments, "
                "document formats, and previous handling instructions. "
                "Digest the retrieved context and answer from the RAG data."
            )

        answer, table_str = _RAG_ENGINE_CACHE[engine_name].answer_question(query)
        if table_str:
            print(f"\n[RAG] FAISS similarity table:\n\n{table_str}\n")

    except Exception as e:
        print(f"RAG Retrieval or Generation failed: {e}")
        return RetrievalResult(used=False, context=None, source=None)

    if answer:
        return RetrievalResult(used=True, context=answer, source="rag")
    return RetrievalResult(used=False, context=None, source=None)


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
        retrieval = rag_retrieval(route, subject_norm, body_norm)

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

    # Step4 — reflection (currently disabled, NO-OP)
    draft = step4_reflect(draft, enabled=False)

    review_id = (email.message_id or "").strip() or str(email.uid)

    return {
        "review_id": review_id,
        "draft": draft,
        "allowed_actions": ["OVERRIDE", "REJECT"],
        "version": edit_version,
    }

# -----------------------------------------------------------------------------
# Step4: Reflection (EMPTY SHELL)
# -----------------------------------------------------------------------------

def step4_reflect(
    draft: str,
    *,
    enabled: bool = False,
) -> str:
    """
    # IMPORTANT:
    # Step4 must remain a pure post-processing hook.
    # Do NOT move routing, retrieval, or generation logic here.

    Currently a NO-OP placeholder.
    When disabled (default), returns draft unchanged.

    This function MUST:
    - NOT modify routing or retrieval
    - NOT call LLM
    - NOT introduce new content
    """
    if not enabled:
        return draft

    # Future reflection logic will be inserted here.
    return draft



def render_review(
    review_obj: dict[str, str | list[str] | int],
) -> str:
    """
    Render the review package into the final email body.

    Today this is intentionally a thin wrapper that formats the review protocol
    around `review_obj["draft"]`.
    """
    header = REVIEW_HEADER_LINE_TEMPLATE.format(version=review_obj["version"])
    footer = REVIEW_FOOTER_LINE
    return f"{header}\n{review_obj['draft']}\n{footer}"
