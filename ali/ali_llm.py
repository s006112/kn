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

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helper.helper_config import load_prompt_text
from helper.helper_llm import call_llm
from helper.utils_imap_types import EmailMessage
from rag.helper_rag_pipeline import get_rag_engine
from ali.ali_parse import (
    REVIEW_FOOTER_LINE,
    REVIEW_HEADER_LINE_TEMPLATE,
    extract_reviewer_reply_text,
    normalize_email_input,
)


RAG_ENGINE_BY_CATEGORY = {
    "safety": "standard",
    "rita": "rita",
}


# -----------------------------------------------------------------------------
# Step2: Routing & RAG
# -----------------------------------------------------------------------------

def route_email(subject: str, body: str) -> str:
    """
    依 email 文字选择 RAG category；未命中则回传 unknown。
    """
    text = f"{subject}\n{body}".lower()

    if re.search(r"\b(iec|ul|nec|csa|tests?|testing|certif\w*|compl(?:y|i\w*)|standard\w*|lumin\w*?)\b",text):
        return "safety"

    if re.search(r"\b(rita\w*)\b", text):
        return "rita"

    return "unknown"


def rag_retrieval(category: str, subject: str, body: str, *, model: str) -> str | None:
    engine_name = RAG_ENGINE_BY_CATEGORY.get(category)
    if engine_name is None:
        return None

    query = "\n\n".join(
        part for part in (f"Subject: {subject}" if subject else "", body) if part
    ).strip()

    try:
        answer, table_str = get_rag_engine(engine_name).answer_question(query, model=model)
        if table_str:
            print(f"\n[RAG] FAISS similarity table:\n\n{table_str}\n")
        return answer.strip() if answer else None
    except Exception as e:
        print(f"RAG Retrieval or Generation failed: {e}")
        return None


# -----------------------------------------------------------------------------
# Step3: 草稿生成（v1 重写，v2+ 仅编辑）
# -----------------------------------------------------------------------------

def generate_review_package(
    email: EmailMessage,
    *,
    system_prompt_path: Path,
    model: str,
    previous_draft: str | None = None,
    edit_version: int = 1,
) -> dict[str, str | list[str] | int]:
    """
    生成内部 review package。

    `previous_draft=None` 走 v1 首次生成路径；传入 previous draft 时走 v2+
    仅编辑路径。
    """
    subject_norm, body_norm = normalize_email_input(email)

    if previous_draft is None:
        category = route_email(subject_norm, body_norm)
        retrieval_context = rag_retrieval(category, subject_norm, body_norm, model=model)

        if retrieval_context is not None:
            draft = retrieval_context
        else:
            system_prompt = load_prompt_text(system_prompt_path.parent, system_prompt_path.name)
            if system_prompt is None:
                raise FileNotFoundError(f"Prompt file not found: {system_prompt_path}")

            draft = call_llm(
                model=model,
                system_prompt=system_prompt,
                user_text="\n\n".join(
                    part
                    for part in (
                        f"Subject: {subject_norm}" if subject_norm else "",
                        body_norm,
                    )
                    if part
                ),
                file_path=None,
            ).strip()
    else:
        system_prompt = load_prompt_text(system_prompt_path.parent, system_prompt_path.name)
        if system_prompt is None:
            raise FileNotFoundError(f"Prompt file not found: {system_prompt_path}")

        reviewer_reply_text = body_norm.strip()
        revision_system_prompt = (
            "Act as the company’s internal email assistant named Ali.\n"
            "Your task is to revise Ali's previous draft according to the reviewer's request.\n"
            "Return only the complete revised email body that will be sent.\n"
            "Keep the reply professional, concise, polite, and specific.\n"
            "Do not include headers such as From, To, or Subject.\n"
            "Do not include meta-comments, explanations, notes, diffs, or quoted email history.\n"
            "Do not fabricate or add new facts unless the reviewer explicitly requests them.\n"
            "Never state, suggest, or imply that you are an AI system.\n\n"
            "REVIEWER-GUIDED REVISION:\n"
            "- Use PREVIOUS_DRAFT as the base response to revise.\n"
            "- Treat REVIEWER_REPLY_TEXT as the reviewer's revision request, whatever word or phrase.\n"
            "- Infer the intended revision from REVIEWER_REPLY_TEXT and PREVIOUS_DRAFT.\n"
            "- Rrevise PREVIOUS_DRAFT accordingly while preserving the original meaning.\n"
            "- Return one complete revised Ali response only."
        )
        draft = call_llm(
            model=model,
            system_prompt=revision_system_prompt,
            user_text=(
                "<PREVIOUS_DRAFT>\n"
                f"{previous_draft.strip()}\n"
                "</PREVIOUS_DRAFT>\n\n"
                "<REVIEWER_REPLY_TEXT>\n"
                f"{reviewer_reply_text}\n"
                "</REVIEWER_REPLY_TEXT>\n\n"
                "Return the complete revised Ali response only."
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
# Step4: 复核（空壳）
# -----------------------------------------------------------------------------

def step4_review(
    draft: str,
    *,
    enabled: bool = False,
) -> str:
    """
    预留的生成后复核 hook，当前保持 NO-OP。

    Step4 不应重新 route、检索、调用 LLM，或引入新内容。
    """
    if not enabled:
        return draft

    # 未来的复核逻辑放在这里。
    return draft


# -----------------------------------------------------------------------------
# Step5: 打包
# -----------------------------------------------------------------------------

def render_review(
    review_obj: dict[str, str | list[str] | int],
) -> str:
    """
    将 review package 渲染为最终邮件正文。

    当前这里只是一个轻量包装，在 `review_obj["draft"]` 外套上 review protocol。
    """
    header = REVIEW_HEADER_LINE_TEMPLATE.format(version=review_obj["version"])
    footer = REVIEW_FOOTER_LINE
    return f"{header}\n{review_obj['draft']}\n{footer}"
