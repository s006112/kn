import re


def clean_overlay(text: str) -> str:
    """
    專門去除 UL / IEC 等安規 PDF 產出的頁眉、頁腳、橫幅等覆蓋文字。
    完全不修改其他字符、不壓縮空白、不影響 sanitize_text 的正常運作。
    """
    if not text:
        return text
    patterns = [
        # 日期 + 年份 + 可選 UL 編號，例如：JANUARY 31, 2025 - UL20
        # 不強制行首行尾，只要行內出現就視為覆蓋文字
        r"[A-Z][A-Z ]+\s+\d{1,2},\s+\d{4}(?:\s*[–-]\s*UL\s*\d+[A-Za-z]?)?",
        r"Document Was Downloaded By .*",
        r"NOT AUTHORIZED FOR FURTHER.*",
        r"REPRODUCTION OR DISTRIBUTION WITHOUT .*",
    ]
    overlay_regex = re.compile("|".join(patterns), flags=re.IGNORECASE)
    # 改為：只要某行中出現覆蓋文字，整行刪除
    lines = text.splitlines(keepends=True)
    kept_lines = [line for line in lines if not overlay_regex.search(line)]
    cleaned = "".join(kept_lines)
    return cleaned


# === 分頁相關工具（供 txt_to_splited_txt 等模塊調用）===

PAGE_BREAK_PREFIX = "<<<PAGE_BREAK_"  # 實際輸出：<<<PAGE_BREAK_2>>>


def is_ul_header_line(s: str) -> bool:
    """
    判斷是否為包含 UL 頁碼的頁眉行。

    規則：
    - 完整匹配 "UL xxx"
    - 或者以 "NMX-J" 開頭，且內容中包含 "UL"
    """
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
    """
    規則（更新版）：
    - 兩行一組，只要同時出現：
        * 一行是純數字 N
        * 另一行是 "UL xxx"
      不管順序是「N 在上 UL 在下」還是「UL 在上 N 在下」
      都把這兩行整組替換成：
        <<<PAGE_BREAK_N>>>
    - 其他行原樣保留
    - 不依賴日期行
    """
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()

        # Case 1: 當前行是純數字，下一行是 "UL xxx"
        if s.isdigit() and i + 1 < len(lines):
            next_line = lines[i + 1]
            ns = next_line.strip()
            if is_ul_header_line(ns):
                page_no = s
                out_lines.append(f"{PAGE_BREAK_PREFIX}{page_no}>>>")
                i += 2
                continue

        # Case 2: 當前行是 "UL xxx"，下一行是純數字
        if is_ul_header_line(s) and i + 1 < len(lines):
            next_line = lines[i + 1]
            ns = next_line.strip()
            if ns.isdigit():
                page_no = ns
                out_lines.append(f"{PAGE_BREAK_PREFIX}{page_no}>>>")
                i += 2
                continue

        # 默認：不屬於頁眉，就原樣輸出
        out_lines.append(line)
        i += 1

    return "\n".join(out_lines)
