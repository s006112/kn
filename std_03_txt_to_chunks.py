#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import json, re, sys

ROOT = Path("data/raw/standard")
OUTPUT = Path("data/json")
IN_SUFFIX = ".page_splited"
OUT_SUFFIX = ".page_blocks.jsonl"
PAGE_RE = re.compile(r"^<<<PAGE_BREAK_(\d+)>>>$")

def main() -> None:
    if not ROOT.exists():
        print(f"[ERROR] {ROOT} not found", file=sys.stderr)
        sys.exit(1)

    OUTPUT.mkdir(parents=True, exist_ok=True)

    for src in sorted(ROOT.rglob(f"*{IN_SUFFIX}")):
        dst = OUTPUT / src.with_suffix(OUT_SUFFIX).name
        file_id = src.stem   # 例如 s935_10.page_splited
        print(f"[INFO] {src} -> {dst}")

        current_page = 0
        buf = []

        with dst.open("w", encoding="utf-8") as out:
            def flush():
                nonlocal buf, current_page
                if not buf:
                    return
                # 从文件名中抽取标准号，如 s1581_4 -> 1581, s50E_4 -> 50E
                m_std = re.match(r"^s(\d+[A-Za-z]?)_\d+$", file_id)
                standard_number = m_std.group(1) if m_std else file_id
                text_body = " ".join(buf).strip()
                if not text_body:
                    buf = []
                    return
                # 为每个块注入标准编号与页码前缀
                injected_prefix = f"UL {standard_number}, page {current_page} "
                text = injected_prefix + text_body
                block = {
                    "block_id": f"{file_id}_p{current_page:04d}",
                    "file_id": file_id,
                    "page": current_page,
                    "char": len(text),
                    "word": len([t for t in text.split() if t]),
                    "text": text,
                }
                out.write(json.dumps(block, ensure_ascii=False) + "\n")
                buf = []

            for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = PAGE_RE.match(line.strip())
                if m:
                    flush()
                    current_page = int(m.group(1))
                else:
                    buf.append(line)
            flush()

if __name__ == "__main__":
    main()
