"""
helper_sanitize.py

Unified sanitization helpers.

This module intentionally contains two categories of sanitizers:
- Flat text sanitization for LLM/chunking (`sanitize_text`) which normalizes Unicode and collapses whitespace.
- Layout-aware sanitization for UL/IEC-style standard TXT (`clean_overlay`, `apply_page_splitting`) which works
  on line structure and inserts page-break markers.
"""

from __future__ import annotations

import re
import unicodedata

# ============================================================================
# Flat text sanitization (LLM/chunking)
# ============================================================================

CHAR_REPLACEMENTS = (
    # Windows-1252 residues kept at the top for clarity，常見於舊 Office 文件
    ("\x00", " "),
    ("\xa0", " "),
    ("\x91", "'"),
    ("\x92", "'"),
    ("\x93", '"'),
    ("\x94", '"'),
    ("\x96", "-"),
    ("\x97", "-"),
    ("\x85", "..."),
    ("\x80", "€"),
    ("\x99", "™"),
    # Unicode variants and compatibility glyphs unified after normalization
    # 將繁多的兼容字元映射為簡潔的 ASCII 以方便後續處理。
    ("“", '"'),
    ("”", '"'),
    ("‘", "'"),
    ("’", "'"),
    ("–", "-"),
    ("—", "-"),
    ("―", "-"),
    ("‒", "-"),
    ("﹣", "-"),
    ("－", "-"),
    ("…", "..."),
    ("⋯", "..."),
    ("•", "•"),
    ("·", "•"),
    ("・", "•"),
    ("‧", "•"),
    ("（", "("),
    ("）", ")"),
    ("［", "["),
    ("］", "]"),
    ("｛", "{"),
    ("｝", "}"),
    ("＼", "\\"),
    ("\ufffd", " "),  # replacement character placeholder
)


CLEAN_REGEXES_GENERAL = [
    (re.compile(r"(?:\b(?:nul|null)\b[\s/\\]*)+", flags=re.IGNORECASE), " "),
    # 表格 & 格式符號污染清理
    (re.compile(r"\|{2,}"), "|"),  # 多個連續的 |
    (re.compile(r"(?:\|\s*){2,}"), " "),  # 多欄位空值合併
    (re.compile(r"^\|\s*$", flags=re.MULTILINE), ""),  # 單獨佔行的 |
    # 常見符號重複壓縮
    (re.compile(r" {2,}"), " "),  # 多空格
    (re.compile(r"\n{2,}"), "\n\n"),  # 多換行
    (re.compile(r"-{2,}"), "-"),
    (re.compile(r"\+{2,}"), "+"),
    (re.compile(r"\.{2}"), "."),
    (re.compile(r"_+"), "_"),
    #    (re.compile(r"\*{2,}"), ""),
    # Aggressive
    (re.compile(r"\*+"), ""),  # * 壓縮
    (re.compile(r"\>+"), ""),
    (re.compile(r"\<+"), ""),
    (re.compile(r"\<+"), ""),
    # 字元正規化
    (re.compile(r"[ \u3000]{2,}"), " "),  # 中文全形空格壓縮
    (re.compile(r"[・‧]{2,}"), "・"),
    (re.compile(r"ͺ{2,}"), "ͺ"),
    # Email 引用符號清理
    (re.compile(r"(?:>\s*){2,}"), ">"),  # 多個連續的 >（含空格）保留單一 >
    # 標點後接英文單字無空格（如 Mr.Smith）
    (re.compile(r"(\w)([.!?])([A-Z])"), r"\1\2 \3"),
    # Invisible control character 清理
    (re.compile(r"[\u200b-\u200f\u202a-\u202e]"), ""),  # Invisible control characters
    # 中文破折號/分隔線異體統一（常見於 OCR）
    (re.compile(r"[─━―]+"), "-"),
    # URL / MAILTO 清理
    (re.compile(r"https?://\S+", flags=re.IGNORECASE), " "),
]

_EMAIL_TOKEN_SPLITTER = re.compile(r"(\s+)")
_EMAIL_STRIP_CHARS = ".,;:!?()[]<>\"'"


def _remove_email_like_phrases(text: str) -> str:
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

    text = unicodedata.normalize("NFKC", text)

    for bad, good in CHAR_REPLACEMENTS:
        text = text.replace(bad, good)

    text = _remove_email_like_phrases(text)

    for regex, repl in CLEAN_REGEXES_GENERAL:
        text = regex.sub(repl, text)

    text = _remove_email_like_phrases(text)

    return re.sub(r"\s+", " ", text).strip()


# ============================================================================
# Layout-aware standard TXT sanitization (overlay removal + page splitting)
# ============================================================================

PAGE_BREAK_PREFIX = "<<<PAGE_BREAK_"  # 實際輸出：<<<PAGE_BREAK_2>>>


def clean_overlay(text: str) -> str:
    if not text:
        return text
    patterns = [
        r"[A-Z][A-Z ]+\s+\d{1,2},\s+\d{4}(?:\s*[–-]\s*UL\s*\d+[A-Za-z]?)?",
        r"Document Was Downloaded By .*",
        r"NOT AUTHORIZED FOR FURTHER.*",
        r"REPRODUCTION OR DISTRIBUTION WITHOUT .*",
    ]
    overlay_regex = re.compile("|".join(patterns), flags=re.IGNORECASE)
    lines = text.splitlines(keepends=True)
    kept_lines = [line for line in lines if not overlay_regex.search(line)]
    cleaned = "".join(kept_lines)
    return cleaned


def is_ul_header_line(s: str) -> bool:
    if re.fullmatch(r"UL\s+\d+[A-Za-z]?", s):
        return True
    if s.startswith("NMX-J") and "UL" in s:
        return True
    if s.startswith("CSA") and re.search(r"\bUL\s+\d+\b", s):
        return True
    if s.startswith("ANSI") and re.search(r"\bUL\s+\d+\b", s):
        return True
    return False


def apply_page_splitting(text: str) -> str:
    lines = text.splitlines()
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()

        if s.isdigit() and i + 1 < len(lines):
            next_line = lines[i + 1]
            ns = next_line.strip()
            if is_ul_header_line(ns):
                page_no = s
                out_lines.append(f"{PAGE_BREAK_PREFIX}{page_no}>>>")
                i += 2
                continue

        if is_ul_header_line(s) and i + 1 < len(lines):
            next_line = lines[i + 1]
            ns = next_line.strip()
            if ns.isdigit():
                page_no = ns
                out_lines.append(f"{PAGE_BREAK_PREFIX}{page_no}>>>")
                i += 2
                continue

        out_lines.append(line)
        i += 1

    return "\n".join(out_lines)

