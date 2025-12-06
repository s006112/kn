import re
from email.message import Message
from typing import Any, List, Optional, Tuple

from bs4 import BeautifulSoup


# ============================================================================
# 文本处理工具
# ============================================================================
# 用于规范化和清理 LLM 输入和输出的辅助函数


# 统一格式化传入的值为字符串。
def _format_text(v: Any) -> str:
    """
    将任何值转换为规范化的文本字符串。
    """
    if v is None:
        return ""
    return v.strip() if isinstance(v, str) else str(v).strip()


# ============================================================================
# HTML <think> 标签处理
# ============================================================================

# 正则表达式模式用于匹配开闭 <think> 标签（不区分大小写）
_THINK_TAG = re.compile(r"<\s*(/?)\s*think\b[^>]*>", re.IGNORECASE)


# 移除 <think> 标签及其中内容，避免污染输出。
def _strip_think(text: str) -> str:
    """
    从文本中移除 <think>...</think> 标签及其内容。
    """
    if not text or "<" not in text:
        return text

    depth, last, out = 0, 0, []

    for m in _THINK_TAG.finditer(text):
        s, e = m.span()
        closing = bool(m.group(1))

        if depth == 0:
            out.append(text[last:s])

        if not closing:
            if depth == 0:
                last = e
            depth += 1
        else:
            if depth > 0:
                depth -= 1
                if depth == 0:
                    last = e
            else:
                out.append(m.group(0))
                last = e

    if depth == 0:
        out.append(text[last:])

    return "".join(out)

# 将 LLM 输出合并为纯文本并过滤 think 标签。
def _normalize_output(content: Any) -> str:
    """
    将 LLM 响应内容转换为干净的字符串。
    """
    if isinstance(content, str):
        text = content.strip()
    elif not content:
        text = ""
    else:
        parts: List[str] = []
        for c in content:
            t = getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else None)
            if t:
                parts.append(str(t))
        text = "\n".join(parts).strip()

    return _strip_think(text)


# ============================================================================
# 邮件正文提取工具
# ============================================================================


# 提取邮件正文内容，先尝试 text/plain，再回退到 text/html。
def extract_email_body(msg: Message) -> str:
    """提取邮件正文内容，优先使用 text/plain，备用 text/html"""

    def _decode_part(part: Message) -> Optional[str]:
        payload = part.get_payload(decode=True)
        if payload is None:
            raw_payload = part.get_payload(decode=False)
            return raw_payload if isinstance(raw_payload, str) else None
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except Exception:
            return payload.decode("utf-8", errors="replace")

    def _html_to_text(content: str) -> str:
        soup = BeautifulSoup(content, "html.parser")
        return soup.get_text(separator="\n", strip=True)

    get_body = getattr(msg, "get_body", None)
    if callable(get_body):
        plain = msg.get_body(preferencelist=("plain",))
        if plain:
            text = plain.get_content()
            if text and text.strip():
                return text.strip()
        html = msg.get_body(preferencelist=("html",))
        if html:
            html_content = html.get_content()
            if html_content:
                return _html_to_text(html_content)
        return ""

    preferred: list[Message] = []
    others: list[Message] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        if part.get_content_type() == "text/plain" and "attachment" not in disp:
            preferred.append(part)
        else:
            others.append(part)

    for part in preferred + others:
        text = _decode_part(part)
        if not text:
            continue
        text = text.strip()
        if not text:
            continue
        return _html_to_text(text) if part.get_content_type() == "text/html" else text

    return ""


# 将邮件正文封装到统一的任务结构中，附上基本 metadata。
def extract_email_body_tasks(
    msg: Message, base_meta: dict, max_len: int
) -> List[Tuple[str, dict]]:
    """提取正文并封装为 task 单元"""
    body = extract_email_body(msg)
    if not body.strip():
        return []
    text = body[:max_len]
    return [
        (
            text,
            {
                **base_meta,
                "part": "body",
                "file_type": "text",  # ✅ 可選
                "attachment": None,
            },
        )
    ]


__all__ = ["_format_text", "_normalize_output", "extract_email_body", "extract_email_body_tasks"]
