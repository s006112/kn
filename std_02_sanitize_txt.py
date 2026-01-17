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
from helper.helper_std_sanitize import clean_overlay, apply_page_splitting
import re
import sys

# === 配置 ===

TXT_ROOT = Path("data/raw/standard/txt")   # chunker.py 輸出的 txt 所在目錄
INPUT_SUFFIX = ".txt"
OUTPUT_SUFFIX = ".page_splited"

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

        # 先做通用頁眉/頁腳/橫幅覆蓋文字清理，再做分頁標記
        cleaned = clean_overlay(text)
        splitted = apply_page_splitting(cleaned)

        out_path = txt_path.with_suffix(OUTPUT_SUFFIX)
        out_path.write_text(splitted, encoding="utf-8")
        print(f"[INFO] Wrote splitted TXT: {out_path}")

    print("[DONE] txt_to_splited_txt finished.")


if __name__ == "__main__":
    main()
