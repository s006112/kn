#!/usr/bin/env python3
"""
Responsibility:
Extracts and sanitizes text from Excel workbooks (XLS/XLSX and variants), merging sheet text into a single document.

Used by:
* rag/chunk_att.py

Pipelines:
- read_excel -> process_sheets -> sanitize_text -> merge_sheets

Invariants:
- Sheet processing is attempted in a `ProcessPoolExecutor` and falls back to sequential processing on failure.

Out of scope:
- Email attachment iteration, routing, and chunk task building (handled by `chunk_att`).
- PDF/Word extraction (handled by `chunk_pdf` / `chunk_doc`).
"""

from __future__ import annotations
import io
import logging
import os
from pathlib import Path
from typing import List, Dict, Optional
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd  # pandas 會用 openpyxl / xlrd 等底層 engine 處理 xlsx/xls
from helper.helper_sanitize import sanitize_text

logger = logging.getLogger(__name__)

XLS_EXTS = {".xls", ".xlsx", ".xlsm", ".xlsb"}


def _process_single_sheet(
    sheet_name: str, df: pd.DataFrame
) -> Optional[tuple[str, str]]:
    """
    Purpose:
    Convert a single sheet DataFrame into a sanitized text block.

    Inputs:
    - sheet_name: Worksheet name.
    - df: Sheet contents as a pandas DataFrame.

    Outputs:
    - `(sheet_name, sanitized_text)` when non-empty; otherwise `None`.

    Side effects:
    - Emits a warning log on processing errors.

    Failure modes:
    - Returns `None` for empty sheets, empty sanitized output, or processing exceptions.
    """

    try:
        df = df.fillna("").astype(str)
        # 向量化每行 tab 连接，避免慢的 apply(lambda ...)
        lines_series = df.agg("\t".join, axis=1)
        full = "\n".join(lines_series.tolist())
        if not full.strip():
            return None
        cleaned = sanitize_text(full)
        if not cleaned:
            return None
        return sheet_name, cleaned
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to process sheet %s: %s", sheet_name, exc)
        return None


def _extract_text_from_excel_bytes(
    data: bytes, filename: str | None = None
) -> Dict[str, str]:
    """
    Purpose:
    Extract and sanitize text from an Excel workbook, returning per-sheet text.

    Inputs:
    - data: Raw workbook bytes.
    - filename: Optional filename used to choose a pandas engine based on suffix.

    Outputs:
    - Mapping of `sheet_name` to sanitized sheet text.

    Side effects:
    - Reads workbook bytes via `pandas.read_excel`.
    - Uses a `ProcessPoolExecutor` for per-sheet processing and logs fallback behavior.

    Failure modes:
    - Returns `{}` when required engines are missing or workbook parsing fails.
    - Falls back to sequential processing when parallel processing fails.
    """

    suffix = Path(filename or "").suffix.lower()
    engine = None
    if suffix in {".xlsx", ".xlsm"}:
        engine = "openpyxl"
    elif suffix == ".xls":
        engine = "xlrd"
    else:
        engine = None  # 退回自动选择

    try:
        with io.BytesIO(data) as bio:
            if engine:
                xl: dict = pd.read_excel(bio, sheet_name=None, dtype=str, engine=engine)
            else:
                xl: dict = pd.read_excel(bio, sheet_name=None, dtype=str)
    except ImportError as ie:
        logger.error(
            "Missing Excel engine for %s (expected %s): %s. Check openpyxl/xlrd.",
            filename or "",
            engine,
            ie,
        )
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to open Excel file %s with engine=%s: %s", filename or "", engine, exc)
        return {}

    sheet_texts: Dict[str, str] = {}

    # 并行处理每个 sheet：适度并发，避免线程爆炸
    max_workers = min(6, os.cpu_count() or 1)
    try:
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_process_single_sheet, name, df): name for name, df in xl.items()
            }
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                except Exception as e:
                    logger.warning("Sheet processing raised: %s", e)
                    continue
                if res:
                    name, cleaned = res
                    sheet_texts[name] = cleaned
    except Exception as e:
        logger.warning("Parallel sheet processing failed (%s), falling back to sequential", e)
        for name, df in xl.items():
            res = _process_single_sheet(name, df)
            if res:
                sheet_texts[res[0]] = res[1]

    logger.info("Excel extraction complete: %d sheets", len(sheet_texts))
    return sheet_texts


def extract_excel_text(data: bytes, filename: str) -> str:
    """
    Purpose:
    Extract and merge sheet text from a single Excel attachment.

    Inputs:
    - data: Attachment bytes.
    - filename: Attachment filename (used for suffix detection and metadata).

    Outputs:
    - Merged text string; returns an empty string when unsupported or empty.

    Side effects:
    - Logs extraction progress and warnings/errors.

    Failure modes:
    - Returns an empty string when extraction fails or yields no text.
    """

    suffix = Path(filename).suffix.lower()
    if suffix not in XLS_EXTS:
        return ""

    sheets = _extract_text_from_excel_bytes(data, filename)
    if not sheets:
        return ""

    # 合并所有 sheet，加入分隔标记
    merged_parts: List[str] = []
    for sheet_name, text in sorted(sheets.items()):
        merged_parts.append(f"=== Sheet: {sheet_name} ===")
        merged_parts.append(text)
    full_text = "\n\n".join(part for part in merged_parts if part and part.strip())

    if not full_text.strip():
        return ""

    logger.info("Extracted Excel attachment %s", filename)
    return full_text
