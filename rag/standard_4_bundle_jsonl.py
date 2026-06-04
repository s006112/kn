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
            out.write(src.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
