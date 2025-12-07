import re
import unicodedata


# ======================================================================
# 專用：UL / IEC / ANSI 標準文件常見水印清除（不干涉其他清洗邏輯）
# ======================================================================

import re

# 可持續擴充
_STANDARD_WATERMARK_PATTERNS = [
    r"Document Was Downloaded By .*?(?:\n|$)",
    r"ULSE INC\. COPYRIGHTED MATERIAL .*?(?:\n|$)",
    r"REPRODUCTION OR DISTRIBUTION WITHOUT PERMISSION FROM ULSE INC\..*?(?:\n|$)",
    r"NOT AUTHORIZED FOR FURTHER REPRODUCTION.*?(?:\n|$)",
]

_STANDARD_WATERMARK_REGEX = re.compile(
    "|".join(_STANDARD_WATERMARK_PATTERNS),
    flags=re.IGNORECASE
)

def clean_watermark(text: str) -> str:
    """
    專門去除 UL / IEC 等安規 PDF 產出的水印。
    完全不修改其他字符、不壓縮空白、不影響 sanitize_text 的正常運作。

    適合放在：
        - PDF → RAW TXT 的最前處
        - sanitize_text() 之前
    """
    if not text:
        return text
    return _STANDARD_WATERMARK_REGEX.sub("", text)


# ======================================================================
# 版面清洗：專門針對 UL / IEC 類安規 TXT 的結構噪音
# ======================================================================

# 領點 + 頁碼，比如 "Scope ..........7"
_UL_TOC_DOTS_RE = re.compile(r"^(.*?)(?:\.+\s*\d+)\s*$")

# 明顯沒用的頁眉 / 頁腳 / 垃圾行
_UL_JUNK_PATTERNS = [
    r"^UL 935\s*$",
    r"^UL Standard for Safety for Fluorescent-Lamp Ballasts, UL 935",
    r"^STANDARD FOR SAFETY",
    r"^Fluorescent-Lamp Ballasts",
    r"^FEBRUARY \d{1,2}, \d{4}",
    r"^No Text on This Page",
    r"^COPYRIGHT",
    r"^ANSI/UL 935-2024.*$",
    r"^Tenth Edition, Dated .*",
    r"^\d+\s*$",        # 單獨的頁碼行
    r"^tr\d+\s*$",     # tr1 / tr2 ...
]
_UL_JUNK_RE = re.compile("|".join(_UL_JUNK_PATTERNS))


def clean_ul_layout(text: str) -> str:
    """
    針對 UL 等安規 TXT 的「版面層」清洗：
    - 去掉目錄中的 ...... + 頁碼，只保留標題
    - 合併「純數字行 + 下一行標題」為一行
    - 刪除明顯的頁眉 / 頁腳 / 垃圾行

    注意：不壓縮整體空白、不打亂行結構，適合在切 block 前使用。
    """
    if not text:
        return ""

    # 1) 去掉 toc 領點 + 頁碼
    lines = []
    for line in text.splitlines():
        m = _UL_TOC_DOTS_RE.match(line)
        if m:
            lines.append(m.group(1).rstrip())
        else:
            lines.append(line)
    text1 = "\n".join(lines)

    # 2) 合併 "13\\nSupply and Load Connections" → "13 Supply and Load Connections"
    raw_lines = text1.splitlines()
    merged: list[str] = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        stripped = line.strip()

        if re.fullmatch(r"\d{1,3}", stripped) and i + 1 < len(raw_lines):
            nxt = raw_lines[i + 1].strip()
            if re.match(r"[A-Za-z]", nxt):
                merged.append(f"{stripped} {nxt}")
                i += 2
                continue

        merged.append(line)
        i += 1

    # 3) 刪除明顯無用的垃圾行
    cleaned: list[str] = []
    for line in merged:
        if _UL_JUNK_RE.match(line.strip()):
            continue
        cleaned.append(line)

    return "\n".join(cleaned)


# ============================================================================
# 文本淨化工具
# ============================================================================
# 專門用於處理不同來源（PDF、Excel、Email 等）輸出的污染文本，
# 將 Windows-1252 遗留字元、控制符號、重複標點、URL 等統一清理後，再交給 LLM。


# === NULL / URL / MAILTO 清理 ===
# === 字元修復表（Unicode & Windows-1252）===
CHAR_REPLACEMENTS = (
    # Windows-1252 residues kept at the top for clarity，常見於舊 Office 文件
    ("\x00", " "), ("\xa0", " "), 
    ("\x91", "'"), ("\x92", "'"),
    ("\x93", '"'), ("\x94", '"'),
    ("\x96", "-"), ("\x97", "-"),
    ("\x85", "..."), ("\x80", "€"),
    ("\x99", "™"),

    # Unicode variants and compatibility glyphs unified after normalization
    # 將繁多的兼容字元映射為簡潔的 ASCII 以方便後續處理。
    ("“", '"'), ("”", '"'),
    ("‘", "'"), ("’", "'"),
    ("–", "-"), ("—", "-"), ("―", "-"), ("‒", "-"), ("﹣", "-"), ("－", "-"),
    ("…", "..."), ("⋯", "..."),
    ("•", "•"), ("·", "•"), ("・", "•"), ("‧", "•"),
    ("（", "("), ("）", ")"), ("［", "["), ("］", "]"), ("｛", "{"), ("｝", "}"),
    ("＼", "\\"),
    ("\ufffd", " "),    # replacement character placeholder 
)

# === 通用格式清洗規則 ===
CLEAN_REGEXES_GENERAL = [
    (re.compile(r'(?:\b(?:nul|null)\b[\s/\\]*)+', flags=re.IGNORECASE), " "),
    # 表格 & 格式符號污染清理
    (re.compile(r"\|{2,}"), "|"),                         # 多個連續的 |
    (re.compile(r"(?:\|\s*){2,}"), " "),                  # 多欄位空值合併
    (re.compile(r"^\|\s*$", flags=re.MULTILINE), ""),     # 單獨佔行的 |

    # 常見符號重複壓縮
    (re.compile(r" {2,}"), " "),                          # 多空格
    (re.compile(r"\n{2,}"), "\n\n"),                      # 多換行
    (re.compile(r"-{2,}"), "-"),
    (re.compile(r"\+{2,}"), "+"),
    (re.compile(r"\.{2}"), "."),
    (re.compile(r"_+"), "_"),
#    (re.compile(r"\*{2,}"), ""),     
    # Aggressive                    
    (re.compile(r"\*+"), ""),                          # * 壓縮
    (re.compile(r"\>+"), ""),
    (re.compile(r"\<+"), ""),
    (re.compile(r"\<+"), ""),

    # 字元正規化
    (re.compile(r"[ \u3000]{2,}"), " "),                  # 中文全形空格壓縮
    (re.compile(r"[・‧]{2,}"), "・"),
    (re.compile(r"ͺ{2,}"), "ͺ"),

    # Email 引用符號清理
    (re.compile(r"(?:>\s*){2,}"), ">"),               # 多個連續的 >（含空格）保留單一 >

    # 標點後接英文單字無空格（如 Mr.Smith）
    (re.compile(r"(\w)([.!?])([A-Z])"), r"\1\2 \3"),

    # Invisible control character 清理
    (re.compile(r"[\u200b-\u200f\u202a-\u202e]"), ""),  # Invisible control characters

    # 中文破折號/分隔線異體統一（常見於 OCR）
    (re.compile(r"[─━―]+"), "-"), 

    # URL / MAILTO 清理
    (re.compile(r'https?://\S+', flags=re.IGNORECASE), " "),
]

_EMAIL_TOKEN_SPLITTER = re.compile(r'(\s+)')
_EMAIL_STRIP_CHARS = ".,;:!?()[]<>\"'"


# 針對 email 正文常見的「請寄到 xxx@yyy.com」等詞彙進行遮罩。
def _remove_email_like_phrases(text: str) -> str:
    parts = _EMAIL_TOKEN_SPLITTER.split(text)
    for idx, part in enumerate(parts):
        if not part or part.isspace():
            continue
        token = part.strip(_EMAIL_STRIP_CHARS)
        if not token:
            continue
        lowered = token.lower()
# heuristics: 確認同時含 @ 與 com（常見商務郵件）才刪除，避免誤刪一般文字
        if "@" in token and "com" in lowered:
            parts[idx] = " "
    return "".join(parts)


# 主入口函數：處理 bytes/str，轉為純淨 Unicode 文本。
def sanitize_text(text: str | bytes) -> str:
    """Return clean Unicode text after removing corruption patterns and legacy encodings."""

    if not text:
        return ""
    if isinstance(text, bytes):
        for enc in ("utf-8", "windows-1252", "iso-8859-1"):
            try:
                text = text.decode(enc)
                break
            except Exception:
                continue
        else:
            text = text.decode("utf-8", errors="replace")

    # === Unicode 正規化 & 控制符號替換 ===
    # NFKC 能將全形/兼容字元轉為標準形式，讓正則規則更好匹配。
    text = unicodedata.normalize("NFKC", text)

    # === 字元處理（正規化後）===
    # 逐個替換掉常見的錯誤字元與 Windows-1252 遺留碼。
    for bad, good in CHAR_REPLACEMENTS:
        text = text.replace(bad, good)

    text = _remove_email_like_phrases(text)

    # === 通用格式清洗（正則套件）===
    # 以正則規則清理 URL、重複標點、表格 artefacts 等。
    for regex, repl in CLEAN_REGEXES_GENERAL:
        text = regex.sub(repl, text)

    text = _remove_email_like_phrases(text)

    # === 結尾清理 ===
    # 最後壓縮所有空白為單一空格，避免破壞語句結構。
    return re.sub(r"\s+", " ", text).strip()
