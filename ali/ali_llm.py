#!/usr/bin/env python3
"""
ali_llm.py

职责：
- 执行 Step2 RAG gating、Step3 draft generation、Step4 review hook 和 Step5 rendering。
- 保持 v1 generation 与 v2+ edit-only path 的边界。

完整 generation contract：
- 见 ali/README.md

Used by:
- ali_email.py
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
from ali.ali_parse import (
    REVIEW_FOOTER_LINE,
    REVIEW_HEADER_LINE_TEMPLATE,
    extract_reviewer_reply_text,
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


# -----------------------------------------------------------------------------
# Step2: Retrieval / Tools
# -----------------------------------------------------------------------------

def rag_retrieval(route: "RouteResult", subject: str, body: str) -> RetrievalResult:
    engine_name = RAG_ENGINE_BY_CATEGORY.get(route.category)
    if engine_name is None:
        return RetrievalResult(used=False, context=None, source=None)

    query = "\n\n".join(part for part in (f"Subject: {subject}" if subject else "", body) if part).strip()
    if route.category == "rita":
        query += (
            "\n\nSearch intent: find relevant historical records from the selected source collection. "
            "Prioritize matching context, participants, dates, content, attachments, "
            "document formats, and previous handling instructions. "
            "Digest the retrieved context and answer from the RAG data."
        )

    try:
        engine = _RAG_ENGINE_CACHE.get(engine_name)
        if engine is None:
            engine = _RAG_ENGINE_CACHE[engine_name] = get_rag_engine(engine_name)
        answer, table_str = engine.answer_question(query)
        if table_str:
            print(f"\n[RAG] FAISS similarity table:\n\n{table_str}\n")
        if answer:
            return RetrievalResult(used=True, context=answer, source="rag")
        return RetrievalResult(used=False, context=None, source=None)
    except Exception as e:
        print(f"RAG Retrieval or Generation failed: {e}")
        return RetrievalResult(used=False, context=None, source=None)


# -----------------------------------------------------------------------------
# Step3: Draft Generation (v1 rewrite, v2+ edit-only)
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
    Generate an INTERNAL review package.

    `previous_draft=None` selects the v1 path. A previous draft selects the
    v2+ edit-only path.
    """
    subject_norm, body_norm = normalize_email_input(email)
    if previous_draft is None:
        route = route_email(subject_norm, body_norm)
        retrieval = rag_retrieval(route, subject_norm, body_norm)
        if retrieval.used:
            draft = retrieval.context.strip()
        else:
            system_prompt = load_prompt_text(system_prompt_path.parent, system_prompt_path.name)
            if system_prompt is None:
                raise FileNotFoundError(f"Prompt file not found: {system_prompt_path}")
            draft = call_llm(
                model=model,
                system_prompt=system_prompt,
                user_text="\n\n".join(part for part in (f"Subject: {subject_norm}" if subject_norm else "", body_norm) if part),
                file_path=None,
            ).strip()
    else:
        edit_prompt_path = system_prompt_path.parent / "prompt_edit_reviewer_reply.txt"
        edit_system_prompt = load_prompt_text(edit_prompt_path.parent, edit_prompt_path.name)
        if edit_system_prompt is None:
            raise FileNotFoundError(f"Reviewer-reply edit prompt not found: {edit_prompt_path}")
        draft = call_llm(
            model=model,
            system_prompt=edit_system_prompt,
            user_text=(
                "previous_draft:\n"
                f"{previous_draft}\n\n"
                "---\n"
                "reviewer_reply_text:\n"
                f"{extract_reviewer_reply_text(body_norm)}"
            ),
            file_path=None,
        ).strip()

    draft = step4_review(draft, enabled=False)
    review_id = (email.message_id or "").strip() or str(email.uid)
    return {
        "review_id": review_id,
        "draft": draft,
        "allowed_actions": ["REPLY", "REJECT"],
        "version": edit_version,
    }

# -----------------------------------------------------------------------------
# Step4: Review (EMPTY SHELL)
# -----------------------------------------------------------------------------

def step4_review(
    draft: str,
    *,
    enabled: bool = False,
) -> str:
    """
    NO-OP placeholder for a pure post-generation hook.

    Step4 must not reroute, retrieve, call LLM, or introduce new content.
    """
    if not enabled:
        return draft

    # Future review logic will be inserted here.
    return draft



# -----------------------------------------------------------------------------
# Step5: Packaging
# -----------------------------------------------------------------------------

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
