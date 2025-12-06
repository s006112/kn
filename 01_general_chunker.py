#!/usr/bin/env python3
"""
General chunker entrypoint.

扫描 project_parent/data/raw_std/*.pdf，
调用 helper/std_chunker 解析条文结构，
输出 JSONL 文件。

入口层只做 I/O，不做任何结构解析逻辑。
"""

import argparse
import sys
from pathlib import Path
from helper.std_chunker import StandardDocInfo, chunk_standard_text  # type: ignore
from helper.utils_pdf import extract_text_from_pdf_bytes             # type: ignore
from rag.chunk_json import JsonlWriter                            # type: ignore


# ---------------------------------------------------------
# 简单 PDF/TXT loader —— 不动结构、不清洗，只把原文给 parser
# ---------------------------------------------------------
def load_text(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".txt":
        return path.read_text(encoding="utf-8")

    if suffix == ".pdf":
        pages = extract_text_from_pdf_bytes(
            path.read_bytes(),
            filename=path.name
        )
        # 保留原始行结构让 parser 判断
        return "\n".join(pages.values())

    raise ValueError(f"Unsupported file type: {path}")


# ---------------------------------------------------------
# 根据 PDF 文件名推断基本 metadata
# ---------------------------------------------------------
def infer_doc_info(path: Path) -> StandardDocInfo:
    stem = path.stem  # 如 UL_935 或 IEC_60598_1
    return StandardDocInfo(
        doc_id=stem,
        doc_code=stem,
    )


# ---------------------------------------------------------
# 主入口：扫描 project_parent/data/raw_std/*.pdf
# ---------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="General clause-level chunker for UL/IEC/EN standards"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/clean_std/std_chunks.jsonl"),
        help="Output JSONL path"
    )
    args = parser.parse_args(argv)

    # -----------------------------------------------------
    # project parent folder = 当前脚本的父目录的父目录
    # 例如：project_root/data/raw_std/*.pdf
    # -----------------------------------------------------
    SCRIPT_DIR = Path(__file__).resolve().parent
    PROJECT_ROOT = SCRIPT_DIR        # <- fix as project root
    raw_std_dir = PROJECT_ROOT / "data" / "raw_std"

    if not raw_std_dir.exists():
        print(f"[ERROR] Folder not found: {raw_std_dir}")
        return 1

    input_paths = sorted(raw_std_dir.glob("*.pdf"))
    if not input_paths:
        print(f"[ERROR] No PDF found under {raw_std_dir}")
        return 1

    print(f"[INFO] Found {len(input_paths)} PDFs under: {raw_std_dir}")

    # -----------------------------------------------------
    # 输出 JSONL
    # -----------------------------------------------------
    args.output.parent.mkdir(parents=True, exist_ok=True)

    total_chunks = 0

    with JsonlWriter(args.output) as writer:
        for path in input_paths:
            print(f"\n[INFO] Processing: {path.name}")

            try:
                text = load_text(path)
            except Exception as exc:
                print(f"[WARN] Could not read {path}: {exc}")
                continue

            info = infer_doc_info(path)
            chunks = chunk_standard_text(text, info)

            if not chunks:
                print(f"[WARN] No clauses detected in {path}")
                continue

            written = writer.write_chunks(chunks)
            total_chunks += written
            print(f"[OK] Wrote {written} chunks for {path.name}")

    print("\n==============================")
    print(f"📦 Total chunks written: {total_chunks}")
    print(f"📁 Output: {args.output.resolve()}")
    print("==============================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
