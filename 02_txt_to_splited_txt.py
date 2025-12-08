#!/usr/bin/env python3
"""
txt_to_splited_txt.py

新版規則：
- 尋找「純數字行 + 'UL xxx' 行」這種組合
- 例如：
    2
    UL 935
  兩行一起替換為：
    <<<PAGE_BREAK_2>>>
- 不判斷 / 依賴任何日期行
"""

from __future__ import annotations

from pathlib import Path
import re
import sys


# === 配置 ===

TXT_ROOT = Path("data/raw/standard")   # chunker.py 輸出的 txt 所在目錄
INPUT_SUFFIX = ".txt"
OUTPUT_SUFFIX = ".page_splited"

# 分頁標記前綴（真正的頁碼會拼在後面）
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
    lines = text.splitlines()
    out_lines: list[str] = []

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


def list_txt_files(root: Path) -> list[Path]:
    """
    遍歷 root 下所有 .txt 文件，排除已經是 .splited.txt 的輸出。
    """
    files: list[Path] = []
    for path in root.rglob(f"*{INPUT_SUFFIX}"):
        if path.name.endswith(OUTPUT_SUFFIX):
            continue
        files.append(path)
    return sorted(files)


def main() -> None:
    if not TXT_ROOT.exists():
        print(f"[ERROR] TXT root not found: {TXT_ROOT}", file=sys.stderr)
        sys.exit(1)

    txt_files = list_txt_files(TXT_ROOT)
    if not txt_files:
        print(f"[INFO] No TXT found under {TXT_ROOT}")
        return

    print(f"[INFO] Found {len(txt_files)} TXT file(s) under {TXT_ROOT}")

    for txt_path in txt_files:
        print(f"[INFO] Processing TXT: {txt_path}")
        text = txt_path.read_text(encoding="utf-8", errors="ignore")

        splitted = apply_page_splitting(text)

        out_path = txt_path.with_suffix(OUTPUT_SUFFIX)
        out_path.write_text(splitted, encoding="utf-8")
        print(f"[INFO] Wrote splitted TXT: {out_path}")

    print("[DONE] txt_to_splited_txt finished.")


if __name__ == "__main__":
    main()
