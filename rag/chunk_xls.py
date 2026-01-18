#!/usr/bin/env python3
"""Excel (XLS/XLSX) text extraction utilities，修复并行/兼容问题并针对多核优化。"""

from __future__ import annotations
import io
import logging
import os
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd  # pandas 會用 openpyxl / xlrd 等底層 engine 處理 xlsx/xls
from chunk_att import build_attachment_tasks, join_nonempty_segments
from helper.helper_sanitize import sanitize_text

logger = logging.getLogger(__name__)

XLS_EXTS = {".xls", ".xlsx", ".xlsm", ".xlsb"}


def _process_single_sheet(
    sheet_name: str, df: pd.DataFrame
) -> Optional[Tuple[str, str]]:
    """
    处理单个 sheet：向量化每行（tab 分隔）、合并整张 sheet 生成文本并 sanitize。
    返回 (sheet_name, sanitized_text) 或 None on failure/empty.
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
    Return {sheet_name: sanitized text} from Excel bytes.
    并行处理每个 sheet 以最大化多核利用。
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


def extract_excel_attachment_tasks(
    data: bytes,
    filename: str,
    content_type: str,
    base_meta: dict,
    max_len: int,
) -> List[Tuple[str, dict]]:
    """Return (text, metadata) chunks for a single Excel attachment.

    Each workbook is treated independently; sheets are merged into one large text
    (with sheet separators) then split to fixed-size chunks.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in XLS_EXTS:
        return []

    sheets = _extract_text_from_excel_bytes(data, filename)
    if not sheets:
        return []

    # 合并所有 sheet，加入分隔标记
    merged_parts: List[str] = []
    for sheet_name, text in sorted(sheets.items()):
        merged_parts.append(f"=== Sheet: {sheet_name} ===")
        merged_parts.append(text)
    full_text = join_nonempty_segments(merged_parts)

    if not full_text.strip():
        return []

    tasks = build_attachment_tasks(
        full_text,
        base_meta=base_meta,
        file_type="excel",
        filename=filename,
        max_len=max_len,
    )

    if tasks:
        logger.info("Extracted Excel attachment %s (%d chunks)", filename, len(tasks))
    return tasks
