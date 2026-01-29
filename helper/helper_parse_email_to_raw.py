#!/usr/bin/env python3
"""
helper_parse_email_to_raw.py
Responsibility:
Convert Email -> RawBlock list (email body + quoted history)
"""

import re
from helper_sanitize import sanitize_text

# 全局开关
ENABLE_QUOTE_SPLIT = True


_QUOTE_SPLIT_PATTERNS = [
    re.compile(r"^<<<QUOTE_START>>>$", re.M),     # HTML blockquote / moz-cite-prefix
    re.compile(r"^发件人", re.M),                 # 业务流水邮件：发件人:
    re.compile(r"^-{3,}.*-{3,}$", re.M),          # -------- 轉寄郵件 -------- / -------- Forwarded Message -------- / ----- Original Message -----
    re.compile(r"^From:\s.+$", re.M),             # From: Kenny Ng <kennyng@ampco.com.hk>
    re.compile(r"^On .+ wrote:\s*$", re.M),       # On Jan 16, 2026, Kenny Ng wrote:
    re.compile(r"^.*於 .+ 寫道:\s*$", re.M),      # Dorothy Lo 於 21/1/2026 17:13 寫道:
]


def _split_body_and_quote(text: str):
    if not ENABLE_QUOTE_SPLIT:
        return text.strip(), ""

    # 最小修复：保证「发件人:」一定在新行
    text = re.sub(r"(发件人:)", r"\n\1", text)

    # 标准化 HTML 引用起点
    text = re.sub(r"<blockquote[^>]*>", "\n<<<QUOTE_START>>>\n", text, flags=re.I)
    text = re.sub(r'<div[^>]*class="moz-cite-prefix"[^>]*>', "\n<<<QUOTE_START>>>\n", text, flags=re.I)

    for pat in _QUOTE_SPLIT_PATTERNS:
        m = pat.search(text)
        if not m:
            continue

        before = text[:m.start()].strip()
        after = text[m.start():].strip()

        after_lines = after.splitlines()
        if len(after_lines) < 2:
            continue

        if not after_lines[1].strip():
            continue

        # 把 <<<QUOTE_START>>> 本身从 quote 里移除
        after = re.sub(r"^<<<QUOTE_START>>>\s*", "", after)

        return before, after

    return text.strip(), ""



def parse_email_to_raw_blocks(email, email_id):
    text_part = email.get_body(preferencelist=("plain", "html"))
    if not text_part:
        return []

    content = text_part.get_content().strip()
    if not content:
        return []

    body, quote = _split_body_and_quote(content)

    blocks = []

    if body:
        blocks.append({
            "doc_id": f"email_{email_id}",
            "text": sanitize_text(body),
            "page": 1,
            "source": "mbox",
            "part": "body",
        })

    if quote:
        blocks.append({
            "doc_id": f"email_{email_id}",
            "text": sanitize_text(quote),
            "page": 1,
            "source": "mbox",
            "part": "quote",
        })

    return blocks
