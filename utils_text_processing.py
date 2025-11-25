import re
from typing import Any, List


# ============================================================================
# 文本处理工具
# ============================================================================
# 用于规范化和清理 LLM 输入和输出的辅助函数


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


__all__ = ["_format_text", "_normalize_output"]

