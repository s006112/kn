#!/usr/bin/env python3
"""
verify_watermark_cleaning.py

用途：
- 随机抽样对比「原始 txt」与「清洗后 txt」
- 只允许水印被删除
- 若发现正文被误删 / 改动，立即报错

前提：
- 原始：XXX.raw.txt
- 清洗后：XXX.txt
"""

from __future__ import annotations

import random
import sys
import re
import difflib
from pathlib import Path


BASE_DIR = Path("data/raw/standard")
SAMPLE_SIZE = 5


WATERMARK_PATTERNS = [
    r"Document Was Downloaded By .*? SUPER X MFG LTD .*?\n?",
    r"ULSE INC\. COPYRIGHTED MATERIAL .*?\n?",
    r"REPRODUCTION OR DISTRIBUTION WITHOUT PERMISSION FROM ULSE INC\.\n?",
]

WATERMARK_REGEX = re.compile("|".join(WATERMARK_PATTERNS), flags=re.IGNORECASE)


def strip_watermark_reference(text: str) -> str:
    """
    用于构造“理论正确结果”，即：
    原始文本 - 水印 = 应等于清洗后文本
    """
    return WATERMARK_REGEX.sub("", text)


def main() -> None:
    raw_files = list(BASE_DIR.rglob("*.raw.txt"))

    if not raw_files:
        print("[ERROR] 找不到任何 *.raw.txt 文件")
        sys.exit(1)

    samples = random.sample(raw_files, min(SAMPLE_SIZE, len(raw_files)))

    for raw_path in samples:
        clean_path = raw_path.with_suffix("").with_suffix(".txt")

        if not clean_path.exists():
            print(f"[ERROR] 缺少清洗后文件: {clean_path}")
            sys.exit(1)

        raw_text = raw_path.read_text(encoding="utf-8", errors="ignore")
        clean_text = clean_path.read_text(encoding="utf-8", errors="ignore")

        expected = strip_watermark_reference(raw_text)

        if expected != clean_text:
            print(f"\n[FAIL] 清洗结果异常: {clean_path}\n")

            diff = difflib.unified_diff(
                expected.splitlines(),
                clean_text.splitlines(),
                fromfile="expected(no watermark)",
                tofile="actual(cleaned)",
                lineterm=""
            )

            for line in diff:
                print(line)

            sys.exit(2)

        else:
            print(f"[OK] {clean_path}")

    print("\n[SUCCESS] 所有抽样文件验证通过，未发现正文被误删")


if __name__ == "__main__":
    main()
