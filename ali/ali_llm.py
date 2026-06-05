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

from helper.helper_llm import call_llm
from helper.utils_imap_types import EmailMessage
from rag.helper_rag_pipeline import get_rag_engine
from ali.ali_parse import (
    REVIEW_FOOTER_LINE,
    REVIEW_HEADER_LINE_TEMPLATE,
    normalize_email_input,
)


RAG_ENGINE_BY_CATEGORY = {
    "safety": "standard",
    "rita": "rita",
}

PROMPT_DIR = Path(__file__).resolve().parent
P1_SYSTEM_PROMPT_PATH = PROMPT_DIR / "prompt_ali_p1_system.txt"
P2_REVISION_PROMPT_PATH = PROMPT_DIR / "prompt_ali_p2_revision.txt"

QUERY_BODY_EXTRACTION_PROMPT = """
Extract the sender's latest meaningful request from the email input.

Return only the query/content that should be answered.
Do not answer the query.
Do not write an email.
Do not include greeting, recipient name, sender name, closing, signature, mobile signature, quoted history, forwarded history, or email headers.
Include the subject only when it contains real request context beyond a generic greeting.
Preserve the original language, technical terms, standard numbers, product names, model numbers, quantities, dates, and all concrete action items.
If there are multiple actionable points, keep them all.
If no meaningful request is found, return the best remaining meaningful content.
""".strip()


# -----------------------------------------------------------------------------
# Email composition
# -----------------------------------------------------------------------------

def _sender_name(email: EmailMessage) -> str:
    name = " ".join(str(getattr(email, "from_name", "") or "").split())
    return "" if not name or "@" in name else name


def _compose_email_body(main_body: str, email: EmailMessage) -> str:
    name = _sender_name(email)
    greeting = f"Hi {name}," if name else "Hi,"
    return f"{greeting}\n\n{main_body.strip()}\n\nRegards,\nAli"


def _strip_email_frame(text: str) -> str:
    lines = (text or "").strip().splitlines()

    if lines and re.match(r"^(hi|hello|dear)\b.*[,，]?$|^(您好|你好)[,，]?$", lines[0].strip(), flags=re.I):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]

    while lines and not lines[-1].strip():
        lines = lines[:-1]
    if len(lines) >= 2 and lines[-1].strip().lower() == "ali" and re.match(r"^(regards|best regards|thanks|thank you|sincerely)[,，]?$", lines[-2].strip(), flags=re.I):
        lines = lines[:-2]
    elif lines and re.match(r"^(regards|best regards|thanks|thank you|sincerely)[,，]?$", lines[-1].strip(), flags=re.I):
        lines = lines[:-1]

    return "\n".join(lines).strip()


def _extract_query_body(email_text: str, *, model: str) -> str:
    source = (email_text or "").strip()
    if not source:
        return ""

    try:
        query = call_llm(
            model=model,
            system_prompt=QUERY_BODY_EXTRACTION_PROMPT,
            user_text=source,
            file_path=None,
        ).strip()
    except Exception as e:
        print(f"Query body extraction failed: {e}")
        return source

    return query or source


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

    email_text = "\n\n".join(
        part
        for part in (
            f"Subject: {subject_norm}" if subject_norm else "",
            body_norm,
        )
        if part
    ).strip()

    print("\n========== ALI DEBUG email_text BEGIN ==========")
    print(email_text)
    print("========== ALI DEBUG email_text END ==========\n")

    if previous_draft is None:
        query_body = _extract_query_body(email_text, model=model)
        print("\n========== ALI DEBUG query_body BEGIN ==========")
        print(query_body)
        print("========== ALI DEBUG query_body END ==========\n")

        category = route_email(subject_norm, query_body)
        retrieval_context = rag_retrieval(category, "", query_body, model=model)

        if retrieval_context is not None:
            main_body = retrieval_context
        else:
            system_prompt = P1_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
            main_body = call_llm(
                model=model,
                system_prompt=system_prompt,
                user_text=query_body,
                file_path=None,
            ).strip()
    else:
        revision_prompt = P2_REVISION_PROMPT_PATH.read_text(encoding="utf-8")
        reviewer_reply_text = body_norm.strip()
        main_body = call_llm(
            model=model,
            system_prompt=revision_prompt,
            user_text=(
                "<PREVIOUS_DRAFT>\n"
                f"{_strip_email_frame(previous_draft)}\n"
                "</PREVIOUS_DRAFT>\n\n"
                "<REVIEWER_REPLY_TEXT>\n"
                f"{reviewer_reply_text}\n"
                "</REVIEWER_REPLY_TEXT>\n\n"
                "Return the revised main reply content only."
            ),
            file_path=None,
        ).strip()

    draft = _compose_email_body(_strip_email_frame(step4_review(main_body, enabled=False)), email)
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
