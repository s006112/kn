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

# Allow running as a script from the repo root without installing the project as a package.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_sanitize import clean_overlay, apply_page_splitting  # noqa: E402

# === 配置 ===

TXT_RAW_DIR = Path("data/standard/txt_raw")
TXT_SPLITTED_DIR = Path("data/standard/txt_splitted")    # chunker.py 輸出的 txt 所在目錄
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
    Run the sanitize pipeline over all TXT files under `TXT_RAW_DIR`.

    Inputs:
    - None (uses module constants).

    Outputs:
    - None (writes output files under `TXT_SPLITTED_DIR`).

    Side effects:
    - Reads input files and writes `.page_splited` outputs.
    - Prints progress to stdout/stderr.
    - Exits with status 1 if `TXT_RAW_DIR` does not exist.

    Failure modes:
    - Exits via `sys.exit(1)` if `TXT_RAW_DIR` is missing.
    - Propagates filesystem-related exceptions from reading/writing files.
    """
    if not TXT_RAW_DIR.exists():
        print(f"[ERROR] TXT root not found: {TXT_RAW_DIR}", file=sys.stderr)
        sys.exit(1)

    txt_files = list_txt_files(TXT_RAW_DIR)
    if not txt_files:
        print(f"[INFO] No TXT found under {TXT_RAW_DIR}")
        return

    print(f"[INFO] Found {len(txt_files)} TXT file(s) under {TXT_RAW_DIR}")
    TXT_SPLITTED_DIR.mkdir(parents=True, exist_ok=True)

    for txt_path in txt_files:
        print(f"[INFO] Processing TXT: {txt_path}")
        text = txt_path.read_text(encoding="utf-8", errors="ignore")

        # Run overlay cleanup first so page-splitting sees only content-relevant lines.
        cleaned = clean_overlay(text)
        splitted = apply_page_splitting(cleaned)

        relative_path = txt_path.relative_to(TXT_RAW_DIR)
        out_path = (TXT_SPLITTED_DIR / relative_path).with_suffix(OUTPUT_SUFFIX)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(splitted, encoding="utf-8")
        print(f"[INFO] Wrote splitted TXT: {out_path}")

    print("[DONE] txt_to_splited_txt finished.")


if __name__ == "__main__":
    main()
