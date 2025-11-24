#!/usr/bin/env python3

from typing import List, Tuple
from email.message import Message

# === 第三方 ===
from bs4 import BeautifulSoup


def extract_email_body(msg: Message) -> str:
    """提取邮件正文内容，优先使用 text/plain，备用 text/html"""
    plain = msg.get_body(preferencelist=('plain'))
    if plain:
        text = plain.get_content()
        if text and len(text.strip()) > 20:
            return text.strip()
    html = msg.get_body(preferencelist=('html'))
    if html:
        html_content = html.get_content()
        if html_content:
            soup = BeautifulSoup(html_content, "html.parser")
            return soup.get_text(separator="\n", strip=True)
    return ""


def extract_email_body_tasks(
    msg: Message,
    base_meta: dict,
    max_len: int
) -> List[Tuple[str, dict]]:
    """提取正文并封装为 task 单元"""
    body = extract_email_body(msg)
    if not body.strip():
        return []
    text = body[:max_len]
    return [(
        text,
        {
            **base_meta,
            "part": "body",
            "file_type": "text",  # ✅ 可選
            "attachment": None,
        },
    )]
