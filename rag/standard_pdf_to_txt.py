#!/usr/bin/env python3
"""

职责：
1. 扫描原始 PDF 目录
2. 调用 helper 模块做原始提取 + 水印清洗
3. 输出 TXT 到同目录
4. 将来可在本文件继续扩展下一个步骤（LLM 标注、结构解析）
"""

from __future__ import annotations

from pathlib import Path
import logging
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# --- helper modules ---
from rag.parse_pdf_to_raw import get_pdf_full_text


# === 配置 ===

RAW_PDF_DIR = Path("data/standard/pdf")   # 原始 PDF 输入目录
TXT_RAW_DIR = Path("data/standard/txt_raw")  # 抽取后的 TXT 输出目录
OUTPUT_SUFFIX = ".txt"                          # 输出格式


# === 内联的目录扫描工具（无需独立 helper） ===

def list_pdfs(root: Path):
    """
    遍历 root 下所有 pdf 文件。
    """
    return sorted(root.rglob("*.pdf"))


# === Pipeline Step 1: PDF → Clean TXT ===

def pdf_to_txt_pipeline() -> None:
    """
    将 PDF 抽取为 TXT（只做水印清洗，不做结构解析）。
    """
    if not RAW_PDF_DIR.exists():
        print(f"[ERROR] Directory not found: {RAW_PDF_DIR}", file=sys.stderr)
        sys.exit(1)

    # 确保输出目录存在
    TXT_RAW_DIR.mkdir(parents=True, exist_ok=True)

    pdf_files = list_pdfs(RAW_PDF_DIR)
    if not pdf_files:
        print(f"[INFO] No PDF found in {RAW_PDF_DIR}")
        return

    for pdf_path in pdf_files:
        print(f"[INFO] Extracting: {pdf_path}")

        raw_text = get_pdf_full_text(pdf_path.read_bytes(), filename=str(pdf_path))

        # 生成相对路径，写入到单独的 TXT 目录
        relative_path = pdf_path.relative_to(RAW_PDF_DIR)
        out_path = (TXT_RAW_DIR / relative_path).with_suffix(OUTPUT_SUFFIX)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(raw_text, encoding="utf-8")

        print(f"[INFO] Wrote TXT: {out_path}")


# === 主入口 ===

def main():
    print("[PIPELINE] Step 1: PDF → TXT")
    pdf_to_txt_pipeline()

    # 未来步骤示范：
    # print("[PIPELINE] Step 2: LLM 标注")
    # run_llm_annotation()
    #
    # print("[PIPELINE] Step 3: 条文结构组装")
    # build_clause_json()
    #
    # print("[PIPELINE] Step 4: 精炼 metadata / 分析")


if __name__ == "__main__":
    main()
