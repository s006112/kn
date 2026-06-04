#!/usr/bin/env python3"
"""
standard_4_bundle_jsonl.py
"""
from pathlib import Path

OUTPUT = Path("data/standard/jsonl")
DST = OUTPUT / "standard_chunks.jsonl"


def main() -> None:
    with DST.open("w", encoding="utf-8") as out:
        for src in sorted(OUTPUT.glob("*_chunks.jsonl")):
            if src == DST:
                continue
            text = src.read_text(encoding="utf-8")
            out.write(text)
            if text and not text.endswith("\n"):
                out.write("\n")


if __name__ == "__main__":
    main()
