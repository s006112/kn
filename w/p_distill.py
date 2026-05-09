"""
p_distill.py - Distillation pipeline for expert extract outputs

Responsibility:
Collect extract outputs, distill them through an LLM prompt, persist the distilled
result, and optionally merge it into a markdown note.

Used by:
* w/p_extract.py

Pipelines:
- collect -> build -> distill -> write -> merge

Invariants:
- Distillation is skipped when MODEL_DISTILL is empty or missing.
- Distill error markers are written to the extract folder on read or LLM failures.

Out of scope:
- Running extraction models or generating source extracts.
- Managing extraction queues or pipeline threading.
"""

import logging
import os
from pathlib import Path
from typing import List, Tuple

from .utils_files import (
    get_next_available_filename,
    release_text_file_permissions,
    read_file_with_encodings,
)
from helper.helper_llm import call_llm
from .utils_md import merge_to_markdown
from .utils_text import sanitize_filename


def _derive_model_label(base_name: str, path: Path) -> str:
    """Derive a model label from an extract filename."""
    stem = path.stem
    suffix = stem[len(base_name) + 1 :] if stem.startswith(f"{base_name}_") else stem
    if "_" in suffix:
        candidate, tail = suffix.rsplit("_", 1)
        if tail.isdigit():
            return candidate
    return suffix or "unknown"


def _write_distill_error(extract_folder: str, base_name: str, message: str) -> None:
    """Write a distillation error marker file to the extract folder."""
    try:
        os.makedirs(extract_folder, exist_ok=True)
        err_path = os.path.join(extract_folder, f"{base_name}_e.distill.error")
        with open(err_path, "w", encoding="utf-8") as ef:
            ef.write(message)
        release_text_file_permissions(err_path)
    except Exception as exc:
        logging.error("Distillation: failed to write error file for %s: %s", base_name, exc)


def _collect_extracts(extract_folder: str, base_name: str, pretext_suffix: str) -> List[Tuple[str, str, str]]:
    """Collect extract labels, contents, and paths for a base name."""
    if not os.path.isdir(extract_folder):
        return []

    prefix = f"{base_name}_"
    suffix = pretext_suffix.lower()
    candidates = sorted(
        fn
        for fn in os.listdir(extract_folder)
        if fn.startswith(prefix) and fn.lower().endswith(suffix)
    )

    extracts: List[Tuple[str, str, str]] = []
    errors: List[str] = []

    for fname in candidates:
        path = os.path.join(extract_folder, fname)
        try:
            content, _ = read_file_with_encodings(path)
            label = _derive_model_label(base_name, Path(path))
            extracts.append((label, content, path))
        except Exception as exc:
            logging.error("Distillation: failed to read extract %s: %s", fname, exc)
            errors.append(fname)

    if errors:
        raise RuntimeError(f"Failed to read extract files for {base_name}: {', '.join(errors)}")

    return extracts


def _build_user_payload(base_name: str, extracts: List[Tuple[str, str, str]]) -> str:
    """Build the user payload for the distillation LLM prompt."""
    lines = [
        f"《{base_name}》",
        "Below are outputs from multiple expert extraction models for the same source. "
        "Please distill them into one final, coherent result according to the system instructions.",
    ]

    for label, content, path in extracts:
        lines.append(f"--- {label} ({os.path.basename(path)}) ---")
        lines.append(content.strip())

    return "\n\n".join(lines)


def run_distillation(config, base_name: str, md_path: str | None = None) -> str | None:
    """Distill multiple extract outputs into a single persisted result."""
    extract_folder = os.fspath(config["EXTRACT_FOLDER"])
    distill_model = (config.get("MODEL_DISTILL") or "").strip()
    distill_suffix = f"_{sanitize_filename(distill_model)}" if distill_model else ""
    intervals = config.get("INTERVALS", {})

    if not distill_model:
        logging.info("Distillation: MODEL_DISTILL not configured, skipping for %s", base_name)
        return None

    try:
        extracts = _collect_extracts(extract_folder, base_name, str(config["PRETEXT_SUFFIX"]))
    except Exception as exc:
        _write_distill_error(extract_folder, base_name, f"Read error: {exc}\n")
        raise

    if not extracts:
        logging.info("Distillation: No extracts found for %s, skipping", base_name)
        return None

    user_payload = _build_user_payload(base_name, extracts)
    logging.info(
        "Distillation: Start %s with %s (%d inputs)",
        base_name,
        distill_model,
        len(extracts),
    )

    try:
        distilled = call_llm(
            model=distill_model,
            system_prompt=config["DISTILL_PROMPT"],
            user_text=user_payload,
            file_path=extracts[0][2],
            max_retries=intervals.get("LLM_MAX_RETRIES", 2),
            timeout=intervals.get("LLM_TIMEOUT_SECONDS", 90),
            retry_delay=intervals.get("LLM_RETRY_DELAY_SECONDS", 10),
        )
    except Exception as exc:
        _write_distill_error(extract_folder, base_name, f"LLM error ({distill_model}): {exc}\n")
        raise

    os.makedirs(extract_folder, exist_ok=True)
    save_path = get_next_available_filename(extract_folder, base_name, distill_suffix)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(distilled)
    release_text_file_permissions(save_path)

    if md_path:
        merge_to_markdown(
            md_path,
            [distilled],
            "",
            [f"{distill_model} distilled"],
            whisper_md_path=os.path.join(config["OBSIDIAN_SYNC_FOLDER"], "Whisper 000000.md"),
            whisper_link_name=Path(md_path).stem,
            md_is_new=False,
        )

    logging.info("Distillation: Completed %s -> %s", base_name, os.path.basename(save_path))
    return save_path
