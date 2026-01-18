#!/usr/bin/env python3
"""
std_02_sanitize_txt.py

Responsibility:
Sanitize raw standard TXT files by removing known PDF overlay/header/footer lines and inserting page-break
markers, then write a sidecar output file per input.

Used by:
* (no direct callers found)

Pipelines:
- discover txt -> read text -> clean overlay -> split pages -> write output

Invariants:
- Does not modify input files in place.
- Reads text with `errors="ignore"` to avoid hard failures on undecodable bytes.

Out of scope:
- PDF extraction and OCR.
- Chunking, embedding, or indexing.
- Defining overlay/page-splitting rules (delegated to `helper.helper_sanitize`).
"""

from __future__ import annotations

from pathlib import Path
import sys
import re  # noqa: F401

# Allow running as a script from the repo root without installing the project as a package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helper.helper_sanitize import clean_overlay, apply_page_splitting  # noqa: E402

# === 配置 ===

TXT_ROOT = Path("data/raw/standard/txt")   # chunker.py 輸出的 txt 所在目錄
INPUT_SUFFIX = ".txt"
OUTPUT_SUFFIX = ".page_splited"

def list_txt_files(root: Path) -> list[Path]:
    """
    Purpose:
    Collect input TXT files under `root`, excluding files that already look like this script's output.

    Inputs:
    - root: Directory to recursively search.

    Outputs:
    - Sorted list of `.txt` `Path` values that do not end with `OUTPUT_SUFFIX`.

    Side effects:
    - None.

    Failure modes:
    - Propagates filesystem-related exceptions raised by `Path.rglob`.
    """
    files: list[Path] = []
    for path in root.rglob(f"*{INPUT_SUFFIX}"):
        if path.name.endswith(OUTPUT_SUFFIX):
            continue
        files.append(path)
    return sorted(files)


def main() -> None:
    """
    Purpose:
    Run the sanitize pipeline over all TXT files under `TXT_ROOT`.

    Inputs:
    - None (uses module constants).

    Outputs:
    - None (writes output files alongside inputs).

    Side effects:
    - Reads input files and writes `.page_splited` outputs.
    - Prints progress to stdout/stderr.
    - Exits with status 1 if `TXT_ROOT` does not exist.

    Failure modes:
    - Exits via `sys.exit(1)` if `TXT_ROOT` is missing.
    - Propagates filesystem-related exceptions from reading/writing files.
    """
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

        # Run overlay cleanup first so page-splitting sees only content-relevant lines.
        cleaned = clean_overlay(text)
        splitted = apply_page_splitting(cleaned)

        out_path = txt_path.with_suffix(OUTPUT_SUFFIX)
        out_path.write_text(splitted, encoding="utf-8")
        print(f"[INFO] Wrote splitted TXT: {out_path}")

    print("[DONE] txt_to_splited_txt finished.")


if __name__ == "__main__":
    main()
