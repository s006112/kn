#!/usr/bin/env python3
"""
llm_responder.py

職責：
- 讀取 prompt/prompt_ali_system.txt 作為 system prompt
- 將 EmailMessage 的內容整理成 user_text 給 LLM
- 使用 utils_llm.call_llm 呼叫 LLM，取得要回信的 email 正文

依賴：
- prompt/prompt_ali_system.txt
- utils_llm.call_llm
- email_fetcher.EmailMessage
"""

from __future__ import annotations
from pathlib import Path

from helper.utils_config import load_prompt_text
from helper.utils_llm import call_llm  # 你的 LLM gateway
from helper.utils_imap_types import EmailMessage  # 前面寫好的 dataclass


def generate_reply(
    email: EmailMessage,
    *,
    system_prompt_path: Path,
    model: str,
) -> str:
    """
    對外主入口：
    - 給一封 EmailMessage
    - 使用指定模型與 prompt 檔案
    - 回傳「要寄回去的 email 正文」（不含標頭）

    參數：
    - system_prompt_path: system prompt 的檔案路徑
    - model: 要呼叫的 LLM 模型名稱
    """
    # 1) 讀 prompt
    system_prompt = load_prompt_text(system_prompt_path.parent, system_prompt_path.name)
    if system_prompt is None:
        raise FileNotFoundError(f"Prompt file not found: {system_prompt_path}")
    # 2) 把主旨與正文組合成 user_text
    parts: list[str] = []
    subject = (email.subject or "").strip()
    if subject:
        parts.append(f"Subject: {subject}")
    body_text = email.body_text or ""
    if body_text:
        parts.append(body_text)
    user_text = "\n\n".join(parts)

    # 3) 呼叫 LLM
    reply_body = call_llm(
        model=model,
        system_prompt=system_prompt,
        user_text=user_text,
        file_path=None,  # 目前不需要 file_path，可留作 debug 用
    )

    return reply_body.strip()
