"""Text processing pipeline runtime and business logic."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from queue import Queue
from typing import List, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_llm import LLMPermanentFailure, call_llm
from .helper_files import (
    get_next_available_filename,
    read_file_with_encodings,
    release_text_file_permissions,
    safe_rename,
)
from .helper_md import (
    create_or_find_note_for_base_name,
    merge_to_markdown,
    write_pretext_markdown,
)
from .helper_text import (
    chunk_text,
    intelligent_merge_chunks,
    sanitize_and_trim_filename,
    sanitize_filename,
)


_file_locks = {}
_file_locks_mutex = threading.Lock()


def request_pretext_processing(queue, processed_files, processed_files_lock, file_path: str) -> bool:
    normalized = os.path.abspath(os.fspath(file_path))
    with processed_files_lock:
        if normalized in processed_files:
            return False
        processed_files.add(normalized)
        queue.put(normalized)
        return True


def release_pretext_request(processed_files, processed_files_lock, file_path: str) -> None:
    normalized = os.path.abspath(os.fspath(file_path))
    with processed_files_lock:
        processed_files.discard(normalized)


def process_pretext_file(config, file_path, processed_files, processed_files_lock) -> None:
    normalized_path = os.path.abspath(os.fspath(file_path))
    intervals = config.get("INTERVALS", {})

    try:
        os.makedirs(config["ORIGINAL_FOLDER"], exist_ok=True)
        if not os.path.exists(normalized_path):
            return

        original_filename = os.path.basename(normalized_path)
        base_name = sanitize_and_trim_filename(os.path.splitext(original_filename)[0])
        original_path = os.path.join(config["ORIGINAL_FOLDER"], f"{base_name}.txt")

        content, encoding_used = read_file_with_encodings(normalized_path)
        logging.info(
            "Pretext: Start %s (characters: %s)",
            original_filename,
            f"{len(content):,}",
        )
        logging.debug(
            "File read successfully using %s encoding, content length: %s",
            encoding_used,
            f"{len(content):,}",
        )

        pretext_model = config["MODEL_PRETEXT"]
        chunks = chunk_text(content)
        logging.info("Pretext: Split into %d chunks", len(chunks))

        all_results = []
        for i, chunk in enumerate(chunks, 1):
            logging.debug(
                "Pretext: API call %d/%d for %s using %s",
                i,
                len(chunks),
                original_filename,
                pretext_model,
            )
            try:
                chunk_result = call_llm(
                    model=pretext_model,
                    system_prompt=config["PRETEXT_PROMPT"],
                    user_text=chunk,
                    file_path=normalized_path,
                    max_retries=intervals.get("LLM_MAX_RETRIES", 2),
                    timeout=intervals.get("LLM_TIMEOUT_SECONDS", 90),
                    retry_delay=intervals.get("LLM_RETRY_DELAY_SECONDS", 10),
                )
            except Exception as exc:
                logging.error(
                    "Pretext API call failed for chunk %d of %s: %s",
                    i,
                    original_filename,
                    exc,
                )
                raise
            if not chunk_result:
                raise ValueError(f"Empty response from OpenAI API for chunk {i}")
            all_results.append(chunk_result)
            logging.debug(
                "Pretext: API call %d/%d successful, response length: %s",
                i,
                len(chunks),
                f"{len(chunk_result):,}",
            )

        pretext_result = intelligent_merge_chunks(all_results)
        if not pretext_result:
            raise ValueError("Empty combined response from OpenAI API")

        logging.info(
            "Pretext: Completed %s (%s : %s)",
            original_filename,
            pretext_model,
            f"{len(pretext_result):,}",
        )

        pretext_target_path = os.path.join(
            config["PRETEXT_WATCH_FOLDER"],
            f"{base_name}{config['EXTRACT_SUFFIX'][0]}",
        )
        with open(pretext_target_path, "w", encoding="utf-8") as f:
            f.write(pretext_result)
        release_text_file_permissions(pretext_target_path)
        logging.info("Pretext: Created %s", os.path.basename(pretext_target_path))

        write_pretext_markdown(config, base_name, pretext_result)
        shutil.move(normalized_path, original_path)

    except Exception as exc:
        logging.error("Error processing file: %s", exc)
        if "pretext_result" in locals():
            error_path = os.path.join(
                config["PRETEXT_WATCH_FOLDER"],
                f"{base_name}.error",
            )
            with open(error_path, "w", encoding="utf-8") as f:
                f.write(f"Error: {exc}\nPartial response:\n{pretext_result}")
            release_text_file_permissions(error_path)
        raise
    finally:
        release_pretext_request(processed_files, processed_files_lock, normalized_path)


def _finalize_extract_success(config, filename: str, base_name: str, md_path: str | None) -> None:
    distill_model = (config.get("MODEL_DISTILL") or "").strip()
    if distill_model:
        logging.info(
            f"Extract: Model {distill_model} for {filename}"
        )
        run_distillation(
            config,
            base_name=base_name,
            md_path=md_path,
        )
        logging.info(
            f"Extract: Completed for {filename} "
        )
    else:
        logging.info(
            f"Extract: Skipped for {filename} (MODEL_DISTILL disabled)"
        )


def _process_extract_file(config, file_path, get_next_available_filename, models, *, enable_distillation):
    """Run configured extraction models, merge results, and archive the source."""
    filename = os.path.basename(file_path)
    logging.info(f"Extract: Start {filename}")
    extract_suffixes = tuple(
        str(s).lower() for s in config["EXTRACT_SUFFIX"] if str(s)
    )

    filename_lower = filename.lower()
    matched_suffix = next((s for s in sorted(extract_suffixes, key=len, reverse=True) if filename_lower.endswith(s)), None)
    base = filename[: -len(matched_suffix)] if matched_suffix else os.path.splitext(filename)[0]

    try:
        content, _ = read_file_with_encodings(file_path)
        payload = f"《{base}》\n{content}"
        intervals = config.get("INTERVALS", {})

        # Avoid repeated note selection so merges stay consistent across models.
        # For markdown-source triggers, write into the existing note with the same filename
        # (no dated note naming), and still ensure it is linked from Whisper 000000.md.
        if matched_suffix == ".md":
            md_path = os.path.join(config["OBSIDIAN_SYNC_FOLDER"], filename)
            link_name = os.path.splitext(filename)[0]
            md_is_new_seed = True
        else:
            md_path, link_name, md_is_new_seed = create_or_find_note_for_base_name(
                config, base, allow_existing=True
            )

        any_success = False
        any_failure = False

        for model in models:
            if not model:
                logging.info(
                    f"Extract: Skipping model entry (not configured)"
                )
                continue
            try:
                # Keep model runs isolated to allow partial results and per-model errors.
                result = call_llm(
                    model=model,
                    system_prompt=config['EXTRACT_PROMPT'],
                    user_text=payload,
                    file_path=file_path,
                    max_retries=intervals.get("LLM_MAX_RETRIES", 2),
                    timeout=intervals.get("LLM_TIMEOUT_SECONDS", 90),
                    retry_delay=intervals.get("LLM_RETRY_DELAY_SECONDS", 10),
                )

                # Preserve raw output before merging to allow later audits.
                os.makedirs(config['EXTRACT_FOLDER'], exist_ok=True)
                model_suffix = f"_{sanitize_filename(model)}"
                save_path = get_next_available_filename(config['EXTRACT_FOLDER'], base, model_suffix)
                with open(save_path, 'w', encoding='utf-8') as f:
                    f.write(result)
                release_text_file_permissions(save_path)

                # Merge immediately to keep the markdown up to date after each success.
                label = f"{model} "
                merge_to_markdown(
                    md_path, [result], "", [label],
                    whisper_md_path=os.path.join(config['OBSIDIAN_SYNC_FOLDER'], 'Whisper 000000.md'),
                    whisper_link_name=link_name,
                    md_is_new=(md_is_new_seed and not any_success)
                )
                any_success = True
                result_chars = len(result)
                logging.info(
                    f"Extract: {filename} "
                    f"({model} : {result_chars:,})"
                )

            except Exception as e:
                any_failure = True
                logging.error(f"Extract: Model {model} failed for {filename}: {e}")
                # Write a per-model error file (best-effort)
                try:
                    os.makedirs(config['PRETEXT_WATCH_FOLDER'], exist_ok=True)
                    err_key = sanitize_filename(model) or "unknown_model"
                    err_path = os.path.join(
                        config['PRETEXT_WATCH_FOLDER'],
                        f"{base}.{err_key}.error",
                    )
                    with open(err_path, 'w', encoding='utf-8') as ef:
                        ef.write(f"Model: {model}\nError: {e}\n")
                    release_text_file_permissions(err_path)
                except Exception as w:
                    logging.error(f"Write per-model error file failed: {w}")

        # Outcome policy:
        # - If ANY model failed -> whole job FAIL (but successful extracts are already merged above).
        # - Else (all succeeded) -> Success.
        if any_failure:
            raise RuntimeError("One or more extraction models failed")

        if enable_distillation:
            _finalize_extract_success(config, filename=filename, base_name=base, md_path=md_path)

        # Premium pipeline archives to a different folder to keep outputs segregated.
        dest_dir = config['PRETEXT_DONE_FOLDER']
        if os.path.abspath(os.path.dirname(file_path)) == os.path.abspath(config['PREMIUM_WATCH_FOLDER']):
            dest_dir = config['ARCHIVE_FOLDER']
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(file_path, os.path.join(dest_dir, filename))
        return

    except Exception as e:
        # If the source file is already moved/missing (common with duplicate queue events),
        # treat it as benign and exit quietly.
        if isinstance(e, FileNotFoundError) or not os.path.exists(file_path):
            #logging.info(f"Extract: Skipping stale item (source missing): {filename}")
            return

        logging.error(f"Error processing {filename}: {e}")
        base_nm = os.path.splitext(filename)[0]
        # Preserve a top-level error marker for troubleshooting.
        try:
            os.makedirs(config['PRETEXT_WATCH_FOLDER'], exist_ok=True)
            err_path = os.path.join(
                config['PRETEXT_WATCH_FOLDER'], f"{base_nm}.error"
            )
            with open(err_path, 'w', encoding='utf-8') as f:
                f.write(f"Error: {e}\n")
            release_text_file_permissions(err_path)
        except Exception as w:
            logging.error(f"Write error file failed: {w}")
        # Only move if the source still exists to avoid duplicate errors.
        try:
            os.makedirs(config['FAIL_FOLDER'], exist_ok=True)
            if os.path.exists(file_path):
                shutil.move(file_path, os.path.join(config['FAIL_FOLDER'], filename))
                logging.info(f"Moved failed file to Fail folder: {filename}")
            else:
                logging.info(f"Fail move skipped; source missing: {filename}")
        except Exception as m:
            logging.error(f"Move to Fail folder failed: {m}")
        raise


def process_extract_file(config, file_path, get_next_available_filename):
    model_matrix = config.get('MODEL_EXTRACT_MATRIX', {})
    models = model_matrix.get('EXTRACT_WATCH_FOLDER', [])
    return _process_extract_file(
        config,
        file_path,
        get_next_available_filename,
        models,
        enable_distillation=True,
    )


def process_premium_extract_file(config, file_path, get_next_available_filename):
    model_matrix = config.get('MODEL_EXTRACT_MATRIX', {})
    models = model_matrix.get('PREMIUM_WATCH_FOLDER', [])
    return _process_extract_file(
        config,
        file_path,
        get_next_available_filename,
        models,
        enable_distillation=False,
    )


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


def scan_pretext_files(config, pretext_queue, processed_files, processed_files_lock) -> None:
    pretext_watch_folder = os.fspath(config["PRETEXT_WATCH_FOLDER"])
    pretext_suffix = str(config["PRETEXT_SUFFIX"]).lower()
    extract_suffixes = tuple(
        str(s).lower() for s in config["EXTRACT_SUFFIX"] if str(s)
    )

    for filename in os.listdir(pretext_watch_folder):
        filename_lower = filename.lower()
        if not filename_lower.endswith(pretext_suffix):
            continue
        file_path = os.path.join(pretext_watch_folder, filename)
        if len(os.path.splitext(filename)[0]) > 60:
            base_name = os.path.splitext(filename)[0]
            sanitized_base = sanitize_and_trim_filename(base_name)
            new_name = sanitized_base + pretext_suffix
            new_path = os.path.join(pretext_watch_folder, new_name)
            try:
                if not os.path.exists(new_path):
                    safe_rename(file_path, new_path)
                    file_path = new_path
                    logging.debug(
                        "Renamed long filename: %s -> %s", filename, new_name
                    )
            except Exception as e:
                logging.error("Error renaming file: %s", e)
                continue

        if filename_lower.endswith(pretext_suffix) and not any(
            filename_lower.endswith(s) for s in extract_suffixes
        ):
            request_pretext_processing(
                pretext_queue,
                processed_files,
                processed_files_lock,
                file_path,
            )


def scan_extract_files(config, extract_queue) -> None:
    extract_watch_folder = os.fspath(config["EXTRACT_WATCH_FOLDER"])
    extract_suffixes = tuple(
        str(s).lower() for s in config["EXTRACT_SUFFIX"] if str(s)
    )

    for filename in os.listdir(extract_watch_folder):
        filename_lower = filename.lower()
        if any(filename_lower.endswith(s) for s in extract_suffixes):
            file_path = os.path.join(extract_watch_folder, filename)
            if file_path not in extract_queue.queue:
                extract_queue.put(file_path)


def scan_premium_extract_files(config, premium_extract_queue) -> None:
    premium_watch_folder = os.fspath(config["PREMIUM_WATCH_FOLDER"])
    extract_suffixes = tuple(
        str(s).lower() for s in config["EXTRACT_SUFFIX"] if str(s)
    )

    for filename in os.listdir(premium_watch_folder):
        filename_lower = filename.lower()
        if any(filename_lower.endswith(s) for s in extract_suffixes):
            file_path = os.path.join(premium_watch_folder, filename)
            if file_path not in premium_extract_queue.queue:
                premium_extract_queue.put(file_path)


def process_queue(config, queue, process, method_name, scan_files=None, shutdown_flag=None, *scan_args):
    intervals = config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)
    scan_seconds = intervals.get("SCAN_SECONDS", 60)
    next_scan = time.monotonic()

    while shutdown_flag is None or not shutdown_flag.is_set():
        if scan_files and time.monotonic() >= next_scan:
            try:
                scan_files(*scan_args)
            except Exception as e:
                logging.error("%s scan error: %s", method_name, e)
            next_scan = time.monotonic() + scan_seconds

        if queue.empty():
            if shutdown_flag is None:
                time.sleep(wait_seconds)
            else:
                shutdown_flag.wait(wait_seconds)
            continue

        file_path = queue.get()
        locked = False

        try:
            with _file_locks_mutex:
                lock = _file_locks.setdefault(file_path, threading.Lock())

            locked = lock.acquire(blocking=False)

            if not locked:
                queue.put(file_path)
            else:
                try:
                    process(file_path, get_next_available_filename)
                except LLMPermanentFailure as e:
                    logging.error(
                        "Resilient Queue: OpenAI API permanent failure for file %s "
                        "(model: %s): %s",
                        e.file_path,
                        e.model,
                        e.reason,
                    )
                except Exception as e:
                    logging.error("%s queue error: %s", method_name, e)

        except Exception as e:
            logging.error("%s queue error: %s", method_name, e)

        finally:
            if locked:
                with _file_locks_mutex:
                    _file_locks.pop(file_path, None)
                lock.release()

            queue.task_done()

        if shutdown_flag is None:
            time.sleep(wait_seconds)
        else:
            shutdown_flag.wait(wait_seconds)


def process_text_pipeline(config, shutdown_flag):
    pretext_queue = Queue()
    extract_queue = Queue()
    premium_extract_queue = Queue()
    processed_files_global = set()
    processed_files_lock = threading.Lock()

    threads = {
        name: threading.Thread(target=target, args=args, daemon=True, name=name)
        for enabled, name, target, args in [
            (config["PIPELINES"]["PRETEXT"], "TextPipeline-Pretext", process_queue, (config, pretext_queue, lambda path, _next: process_pretext_file(config, path, processed_files_global, processed_files_lock), "process_pretext", scan_pretext_files, shutdown_flag, config, pretext_queue, processed_files_global, processed_files_lock)),
            (config["PIPELINES"]["EXTRACT"], "TextPipeline-Extract", process_queue, (config, extract_queue, lambda path, _next: process_extract_file(config, path, _next), "process_extract", scan_extract_files, shutdown_flag, config, extract_queue)),
            (config["PIPELINES"]["PREMIUM_EXTRACT"], "TextPipeline-PremiumExtract", process_queue, (config, premium_extract_queue, lambda path, _next: process_premium_extract_file(config, path, _next), "process_premium_extract", scan_premium_extract_files, shutdown_flag, config, premium_extract_queue)),
        ]
        if enabled
    }

    for thread in threads.values():
        thread.start()

    return threads
