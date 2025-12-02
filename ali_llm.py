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

from utils_config import load_prompt_text
from utils_llm import call_llm  # 你的 LLM gateway
from utils_imap_types import EmailMessage  # 前面寫好的 dataclass


# 預設路徑與模型，可視需要改成從 config 讀取
PATH = Path(__file__).resolve().parent / "prompt" / "prompt_ali_system.txt"

# sonar, sonar-pro, sonar-reasoning, sonar-reasoning-pro
# gemini-2.0-flash, gemini-2.5-flash, gemini-2.5-pro, gemini-3-pro-preview, 
# gpt-5-mini, gpt-5-nano, gpt-4.1-mini, gpt-4.1-nano, gpt-4o-mini, o1-mini, o3-mini, o4-mini, codex-mini-latest
# gpt-5.1, gpt-5, gpt-5-chat-latest, gpt-4.1, gpt-4o, o1, o3,
LLM_MODEL = "sonar"


def build_user_text(email: EmailMessage) -> str:
    """把 EmailMessage 的欄位整理成提供給 LLM 的 user_text。"""
    sender_display = email.from_addr or "Unknown Sender"
    subject = email.subject or ""
    body = email.body_text or ""

    return (
        f"From: {sender_display}\n"
        f"Subject: {subject}\n\n"
        f"Body:\n{body}"
    )


def generate_reply(email: EmailMessage) -> str:
    """
    對外主入口：
    - 給一封 EmailMessage
    - 使用指定模型與 prompt 檔案
    - 回傳「要寄回去的 email 正文」（不含標頭）

    """
    # 1) 讀 prompt
    system_prompt = load_prompt_text(PATH.parent, PATH.name)
    # 2) 整理 user_text
    user_text = build_user_text(email)

    # 3) 呼叫 LLM
    reply_body = call_llm(
        model=LLM_MODEL,
        system_prompt=system_prompt,
        user_text=user_text,
        file_path=None,  # 目前不需要 file_path，可留作 debug 用
    )

    return reply_body.strip()
