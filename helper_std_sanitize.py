import re

# ======================================================================
# 專用：UL / IEC / ANSI 標準文件常見頁眉/頁腳/橫幅等噪音清除（不干涉其他清洗邏輯）
# ======================================================================

# 可持續擴充：各類頁眉/頁腳/橫幅等覆蓋文字樣式
_STANDARD_OVERLAY_PATTERNS = [
    r"Document Was Downloaded By .*?(?:\n|$)",
    r"ULSE INC\. COPYRIGHTED MATERIAL .*?(?:\n|$)",
    r"REPRODUCTION OR DISTRIBUTION WITHOUT PERMISSION FROM ULSE INC\..*?(?:\n|$)",
    r"NOT AUTHORIZED FOR FURTHER REPRODUCTION.*?(?:\n|$)",
]

_STANDARD_OVERLAY_REGEX = re.compile(
    "|".join(_STANDARD_OVERLAY_PATTERNS),
    flags=re.IGNORECASE
)

def clean_overlay(text: str) -> str:
    """
    專門去除 UL / IEC 等安規 PDF 產出的頁眉、頁腳、橫幅等覆蓋文字。
    完全不修改其他字符、不壓縮空白、不影響 sanitize_text 的正常運作。
    """
    if not text:
        return text
    return _STANDARD_OVERLAY_REGEX.sub("", text)
