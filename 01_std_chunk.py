#!/usr/bin/env python3
"""Chunk technical standards into JSONL using clause headings."""

import argparse
import sys
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent / "rag"
if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))

from rag.chunk_json import JsonlWriter  # type: ignore
from std_chunker import StandardDocInfo, chunk_standard_text  # type: ignore
import utils_pdf


def load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        pages = utils_pdf.extract_text_from_pdf_bytes(path.read_bytes(), filename=path.name)
        return "\n".join(pages.values())
    raise ValueError(f"Unsupported file type for {path}. Provide a PDF or UTF-8 .txt file.")


def infer_doc_info(path: Path) -> StandardDocInfo:
    stem = path.stem
    return StandardDocInfo(doc_id=stem, doc_code=stem)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Chunk standards (clause-level) into JSONL. If no inputs are provided, defaults to data/raw/standard/*.pdf"
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="One or more PDF or UTF-8 .txt files.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/clean_std/std_chunks.jsonl"),
        help="Output JSONL path (default: data/clean_std/std_chunks.jsonl)",
    )
    args = parser.parse_args(argv)

    total_chunks = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)

    input_paths = args.inputs
    if not input_paths:
        default_dir = Path("data/raw_std")
        input_paths = sorted(default_dir.glob("*.pdf"))
        if not input_paths:
            print("[ERROR] No input files provided and no default files found in data/raw_std/*.pdf")
            return 1
        print(f"[INFO] No inputs given; using defaults: {', '.join(p.name for p in input_paths)}")

    with JsonlWriter(args.output) as writer:
        for path in input_paths:
            if not path.exists():
                print(f"[WARN] File not found, skipping: {path}")
                continue
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
            print(f"✅ {path.name}: wrote {written} clause chunks")

    print(f"📦 Done. Total chunks written: {total_chunks}")
    print(f"📁 Output: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
