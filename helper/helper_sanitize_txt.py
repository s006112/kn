"""
Responsibility:
Provide a single entry point to sanitize corrupted text (from PDF/Office/email exports)
into a compact Unicode string suitable for downstream LLM processing.

Used by:
* rag/chunk_doc.py
* rag/chunk_json.py
* rag/chunk_pdf.py
* rag/chunk_xls.py

Pipelines:
- decode_bytes -> normalize_nfkc -> replace_chars -> mask_email_tokens -> regex_clean -> mask_email_tokens -> collapse_whitespace

Invariants:
- Returns a `str` for all inputs.
- Returns `""` when `text` is falsy.
- Output is Unicode-normalized (NFKC) and whitespace-collapsed to single spaces.

Out of scope:
- Preserving original line breaks or layout.
- Parsing structured formats (HTML, Markdown, tables) beyond regex cleanup.
- Validating whether an email is real; masking uses a heuristic token rule.
"""

import re
import unicodedata

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


def _remove_email_like_phrases(text: str) -> str:
    """
    Purpose:
    Mask email-like tokens in free text using a heuristic rule.

    Inputs:
    - text: Source text to scan.

    Outputs:
    - A string where some tokens are replaced by a single space.

    Side effects:
    - None.

    Failure modes:
    - None; unexpected inputs raise from string operations.
    """
    parts = _EMAIL_TOKEN_SPLITTER.split(text)
    for idx, part in enumerate(parts):
        if not part or part.isspace():
            continue
        token = part.strip(_EMAIL_STRIP_CHARS)
        if not token:
            continue
        lowered = token.lower()
        # Keep this heuristic conservative to reduce accidental masking of non-email text.
        if "@" in token and "com" in lowered:
            parts[idx] = " "
    return "".join(parts)


def sanitize_text(text: str | bytes) -> str:
    """
    Purpose:
    Normalize and clean text from heterogeneous sources into a compact Unicode string.

    Inputs:
    - text: `str` or raw `bytes`. For `bytes`, decoding is attempted in a fixed order.

    Outputs:
    - A whitespace-collapsed `str` with selected legacy/control characters removed or replaced.

    Side effects:
    - None.

    Failure modes:
    - If `text` is not `str | bytes`, operations like `isinstance`/`unicodedata.normalize` may raise.
    """

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
