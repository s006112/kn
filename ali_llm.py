#!/usr/bin/env python3
"""
llm_responder.py

職責：
- 讀取 prompt/prompt_ali_system.txt 作為 system prompt
- 讀取 prompt/prompt_ali_user.txt 作為 user prompt 模板
- 將 EmailMessage 套入 user prompt 模板
- 使用 utils_llm.call_llm 呼叫 LLM，取得要回信的 email 正文

依賴：
- prompt/prompt_ali_system.txt
- prompt/prompt_ali_user.txt
- utils_llm.call_llm
- email_fetcher.EmailMessage
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from utils_llm import call_llm  # 你的 LLM gateway
from ali_fetch import EmailMessage  # 前面寫好的 dataclass


# 預設路徑與模型，可視需要改成從 config 讀取
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parent))
PROMPT_DIR = PROJECT_ROOT / "prompt"

DEFAULT_SYSTEM_PROMPT_PATH = PROMPT_DIR / "prompt_ali_system.txt"
DEFAULT_USER_PROMPT_PATH = PROMPT_DIR / "prompt_ali_user.txt"

# sonar, sonar-pro, sonar-reasoning, sonar-reasoning-pro
# gemini-2.0-flash, gemini-2.5-flash, gemini-2.5-pro, gemini-3-pro-preview, 
# gpt-5-mini, gpt-5-nano, gpt-4.1-mini, gpt-4.1-nano, gpt-4o-mini, o1-mini, o3-mini, o4-mini, codex-mini-latest
# gpt-5.1, gpt-5, gpt-5-chat-latest, gpt-4.1, gpt-4o, o1, o3,
DEFAULT_MODEL = "sonar"
DEFAULT_ASSISTANT_EMAIL = os.getenv("ALI_ASSISTANT_EMAIL", "assistant@company.com")


# 簡單的快取，避免每封信都重複讀檔
@dataclass
class _PromptCache:
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None


_PROMPT_CACHE = _PromptCache()


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def load_system_prompt(path: Optional[Path] = None) -> str:
    """
    讀取 system prompt 文字。
    預設使用 prompt/prompt_ali_system.txt
    """
    global _PROMPT_CACHE
    if _PROMPT_CACHE.system_prompt is not None and path is None:
        return _PROMPT_CACHE.system_prompt

    p = path or DEFAULT_SYSTEM_PROMPT_PATH
    text = _read_text(p)
    if path is None:
        _PROMPT_CACHE.system_prompt = text
    return text


def load_user_prompt_template(path: Optional[Path] = None) -> str:
    """
    讀取 user prompt 模板文字。
    預設使用 prompt/prompt_ali_user.txt
    """
    global _PROMPT_CACHE
    if _PROMPT_CACHE.user_prompt_template is not None and path is None:
        return _PROMPT_CACHE.user_prompt_template

    p = path or DEFAULT_USER_PROMPT_PATH
    text = _read_text(p)
    if path is None:
        _PROMPT_CACHE.user_prompt_template = text
    return text


def build_user_text_from_template(
    email: EmailMessage,
    *,
    assistant_email: Optional[str] = None,
    template: Optional[str] = None,
) -> str:
    """
    用 prompt_ali_user.txt 的模板把 EmailMessage 組成 LLM 的 user_text。

    模板內容大致如下（簡化示意）：
    Here is an email you received from a colleague.
    ...
    From: {sender_address_or_name}
    To: {assistant_email}
    Subject: {original_subject}

    Body:
    {original_email_body}
    """
    tmpl = template or load_user_prompt_template()
    assistant_addr = assistant_email or DEFAULT_ASSISTANT_EMAIL

    sender_display = email.from_addr or "Unknown Sender"
    subject = email.subject or ""
    body = email.body_text or ""

    # 關鍵：套用模板中的 format 占位符
    user_text = tmpl.format(
        sender_address_or_name=sender_display,
        assistant_email=assistant_addr,
        original_subject=subject,
        original_email_body=body,
    )
    return user_text


def generate_reply(
    email: EmailMessage,
    *,
    model: Optional[str] = None,
    assistant_email: Optional[str] = None,
    system_prompt_path: Optional[Path] = None,
    user_prompt_path: Optional[Path] = None,
) -> str:
    """
    對外主入口：
    - 給一封 EmailMessage
    - 使用指定模型與 prompt 檔案
    - 回傳「要寄回去的 email 正文」（不含標頭）

    參數：
    - model: 若為 None 則使用 DEFAULT_MODEL
    - assistant_email: 若為 None 則使用 DEFAULT_ASSISTANT_EMAIL
    - system_prompt_path / user_prompt_path: 若需自訂路徑可傳入 Path
    """
    # 1) 讀 prompt
    system_prompt = load_system_prompt(system_prompt_path)
    user_template = load_user_prompt_template(user_prompt_path)

    # 2) 根據模板組裝 user_text
    user_text = build_user_text_from_template(
        email,
        assistant_email=assistant_email,
        template=user_template,
    )

    # 3) 呼叫 LLM
    model_name = (model or DEFAULT_MODEL).strip()
    reply_body = call_llm(
        model=model_name,
        system_prompt=system_prompt,
        user_text=user_text,
        file_path=None,  # 目前不需要 file_path，可留作 debug 用
    )

    return reply_body.strip()
