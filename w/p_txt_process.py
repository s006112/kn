from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from queue import Queue

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_llm import LLMPermanentFailure, call_llm
from .helper_files import get_next_available_filename, read_file_with_encodings, release_text_file_permissions, safe_rename
from .helper_md import create_or_find_note_for_base_name, merge_to_markdown, write_pretext_markdown
from .helper_text import chunk_text, intelligent_merge_chunks, sanitize_and_trim_filename, sanitize_filename


_file_locks = {}
_file_locks_mutex = threading.Lock()


def llm_call_options(config):
    return {
        "max_retries": config["INTERVALS"].get("LLM_MAX_RETRIES", 2),
        "timeout": config["INTERVALS"].get("LLM_TIMEOUT_SECONDS", 90),
        "retry_delay": config["INTERVALS"].get("LLM_RETRY_DELAY_SECONDS", 10),
    }


def write_text_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    release_text_file_permissions(path)
    return path


def save_pipeline_error(config, stage, base_name, error, *, filename=None, model=None, partial=None):
    marker = sanitize_filename(model or stage) or "unknown"
    path = os.path.join(config["WATCH_FOLDER"], f"{base_name}.{marker}.error")
    logging.error("Pipeline error | stage=%s file=%s base=%s model=%s error=%s", stage, filename or "", base_name, model or "", error)

    lines = [
        f"Stage: {stage}",
        f"File: {filename or ''}",
        f"Base: {base_name}",
        f"Model: {model or ''}",
        f"Error: {error}",
    ]
    if partial is not None:
        lines += ["", "Partial:", str(partial)]

    try:
        os.makedirs(config["WATCH_FOLDER"], exist_ok=True)
        return write_text_file(path, "\n".join(lines).rstrip() + "\n")
    except Exception as write_error:
        logging.error("Pipeline error write failed | target=%s error=%s", path, write_error)
        return None


def process_pretext_file(config, file_path, processed_files, processed_files_lock) -> None:
    normalized_path = os.path.abspath(os.fspath(file_path))
    original_filename = os.path.basename(normalized_path)
    base_name = sanitize_and_trim_filename(os.path.splitext(original_filename)[0])
    pretext_model = config["MODEL_PRETEXT"]

    try:
        os.makedirs(config["ORIGINAL_FOLDER"], exist_ok=True)
        if not os.path.exists(normalized_path):
            return

        original_path = os.path.join(config["ORIGINAL_FOLDER"], f"{base_name}.txt")
        content, encoding_used = read_file_with_encodings(normalized_path)
        logging.info("Pretext: Start %s (characters: %s)", original_filename, f"{len(content):,}")
        logging.debug("File read successfully using %s encoding, content length: %s", encoding_used, f"{len(content):,}")

        chunks = chunk_text(content)
        logging.info("Pretext: Split into %d chunks", len(chunks))

        all_results = []
        for i, chunk in enumerate(chunks, 1):
            logging.debug("Pretext: API call %d/%d for %s using %s", i, len(chunks), original_filename, pretext_model)
            chunk_result = call_llm(model=pretext_model, system_prompt=config["PRETEXT_PROMPT"], user_text=chunk, file_path=normalized_path, **llm_call_options(config))
            if not chunk_result:
                raise ValueError(f"Empty response from OpenAI API for chunk {i}")
            all_results.append(chunk_result)
            logging.debug("Pretext: API call %d/%d successful, response length: %s", i, len(chunks), f"{len(chunk_result):,}")

        pretext_result = intelligent_merge_chunks(all_results)
        if not pretext_result:
            raise ValueError("Empty combined response from OpenAI API")

        logging.info("Pretext: Completed %s (%s : %s)", original_filename, pretext_model, f"{len(pretext_result):,}")

        pretext_target_path = os.path.join(config["PRETEXT_WATCH_FOLDER"], f"{base_name}{config['EXTRACT_SUFFIX']}")
        write_text_file(pretext_target_path, pretext_result)
        logging.info("Pretext: Created %s", os.path.basename(pretext_target_path))

        write_pretext_markdown(config, base_name, pretext_result)
        shutil.move(normalized_path, original_path)

    except Exception as exc:
        save_pipeline_error(config, "pretext", base_name, exc, filename=original_filename, model=pretext_model, partial=locals().get("pretext_result"))
        raise
    finally:
        with processed_files_lock:
            processed_files.discard(normalized_path)


def process_extract_file(config, file_path):
    filename = os.path.basename(file_path)
    extract_suffix = str(config["EXTRACT_SUFFIX"]).lower()
    base = filename[: -len(extract_suffix)] if filename.lower().endswith(extract_suffix) else os.path.splitext(filename)[0]
    failed_models = []

    try:
        logging.info(f"Extract: Start {filename}")
        content, _ = read_file_with_encodings(file_path)
        payload = f"《{base}》\n{content}"
        md_path, link_name, md_is_new_seed = create_or_find_note_for_base_name(config, base, allow_existing=True)

        extract_count = 0
        for model in config.get("MODEL_EXTRACT_MATRIX", {}).get("EXTRACT_WATCH_FOLDER", []):
            if not model:
                logging.info("Extract: Skipping model entry (not configured)")
                continue

            try:
                result = call_llm(model=model, system_prompt=config["EXTRACT_PROMPT"], user_text=payload, file_path=file_path, **llm_call_options(config))
                os.makedirs(config["EXTRACT_FOLDER"], exist_ok=True)
                save_path = get_next_available_filename(config["EXTRACT_FOLDER"], base, f"_{sanitize_filename(model)}")
                write_text_file(save_path, result)
                merge_to_markdown(md_path, [result], "", [f"{model} "], whisper_md_path=os.path.join(config["OBSIDIAN_SYNC_FOLDER"], "Whisper 000000.md"), whisper_link_name=link_name, md_is_new=(md_is_new_seed and extract_count == 0))
                extract_count += 1
                logging.info(f"Extract: {filename} ({model} : {len(result):,})")
            except Exception as exc:
                failed_models.append(model)
                save_pipeline_error(config, "extract", base, exc, filename=filename, model=model)

        if failed_models:
            raise RuntimeError("One or more extraction models failed")

        distill_model = (config.get("MODEL_DISTILL") or "").strip()
        if distill_model:
            logging.info(f"Extract: Model {distill_model} for {filename}")
            try:
                run_distillation(config, base_name=base, md_path=md_path)
            except Exception:
                failed_models.append(distill_model or "distill")
                raise
            logging.info(f"Extract: Completed for {filename} ")
        else:
            logging.info(f"Extract: Skipped for {filename} (MODEL_DISTILL disabled)")

        os.makedirs(config["PRETEXT_DONE_FOLDER"], exist_ok=True)
        shutil.move(file_path, os.path.join(config["PRETEXT_DONE_FOLDER"], filename))
        return

    except Exception as exc:
        if isinstance(exc, FileNotFoundError) or not os.path.exists(file_path):
            return

        if not failed_models:
            save_pipeline_error(config, "extract", base, exc, filename=filename, model="extract")

        try:
            os.makedirs(config["FAIL_FOLDER"], exist_ok=True)
            if os.path.exists(file_path):
                shutil.move(file_path, os.path.join(config["FAIL_FOLDER"], filename))
                logging.info(f"Moved failed file to Fail folder: {filename}")
            else:
                logging.info(f"Fail move skipped; source missing: {filename}")
        except Exception as move_error:
            logging.error(f"Move to Fail folder failed: {move_error}")
        raise


def collect_extracts(extract_folder: str, base_name: str, pretext_suffix: str):
    if not os.path.isdir(extract_folder):
        return []

    extracts = []
    errors = []
    prefix = f"{base_name}_"
    suffix = pretext_suffix.lower()

    for fname in sorted(fn for fn in os.listdir(extract_folder) if fn.startswith(prefix) and fn.lower().endswith(suffix)):
        path = os.path.join(extract_folder, fname)
        try:
            content, _ = read_file_with_encodings(path)
            label = Path(path).stem
            label = label[len(base_name) + 1 :] if label.startswith(prefix) else label
            if "_" in label:
                candidate, tail = label.rsplit("_", 1)
                label = candidate if tail.isdigit() else label
            extracts.append((label or "unknown", content, path))
        except Exception as exc:
            logging.error("Distillation: failed to read extract %s: %s", fname, exc)
            errors.append(fname)

    if errors:
        raise RuntimeError(f"Failed to read extract files for {base_name}: {', '.join(errors)}")

    return extracts


def run_distillation(config, base_name: str, md_path: str | None = None) -> str | None:
    extract_folder = os.fspath(config["EXTRACT_FOLDER"])
    distill_model = (config.get("MODEL_DISTILL") or "").strip()

    if not distill_model:
        logging.info("Distillation: MODEL_DISTILL not configured, skipping for %s", base_name)
        return None

    try:
        extracts = collect_extracts(extract_folder, base_name, str(config["PRETEXT_SUFFIX"]))
        if not extracts:
            logging.info("Distillation: No extracts found for %s, skipping", base_name)
            return None

        payload = [f"《{base_name}》", "Below are outputs from multiple expert extraction models for the same source. Please distill them into one final, coherent result according to the system instructions."]
        for label, content, path in extracts:
            payload += [f"--- {label} ({os.path.basename(path)}) ---", content.strip()]

        logging.info("Distillation: Start %s with %s (%d inputs)", base_name, distill_model, len(extracts))
        distilled = call_llm(model=distill_model, system_prompt=config["DISTILL_PROMPT"], user_text="\n\n".join(payload), file_path=extracts[0][2], **llm_call_options(config))

        os.makedirs(extract_folder, exist_ok=True)
        save_path = get_next_available_filename(extract_folder, base_name, f"_{sanitize_filename(distill_model)}")
        write_text_file(save_path, distilled)

        if md_path:
            merge_to_markdown(md_path, [distilled], "", [f"{distill_model} distilled"], whisper_md_path=os.path.join(config["OBSIDIAN_SYNC_FOLDER"], "Whisper 000000.md"), whisper_link_name=Path(md_path).stem, md_is_new=False)

        logging.info("Distillation: Completed %s -> %s", base_name, os.path.basename(save_path))
        return save_path

    except Exception as exc:
        save_pipeline_error(config, "distill", base_name, exc, filename=base_name, model=distill_model or "distill")
        raise


def scan_pretext_files(config, pretext_queue, processed_files, processed_files_lock) -> None:
    pretext_watch_folder = os.fspath(config["PRETEXT_WATCH_FOLDER"])
    pretext_suffix = str(config["PRETEXT_SUFFIX"]).lower()
    extract_suffix = str(config["EXTRACT_SUFFIX"]).lower()

    for filename in os.listdir(pretext_watch_folder):
        filename_lower = filename.lower()
        if not filename_lower.endswith(pretext_suffix) or filename_lower.endswith(extract_suffix):
            continue

        file_path = os.path.join(pretext_watch_folder, filename)
        if len(os.path.splitext(filename)[0]) > 60:
            base_name = os.path.splitext(filename)[0]
            new_name = sanitize_and_trim_filename(base_name) + pretext_suffix
            new_path = os.path.join(pretext_watch_folder, new_name)
            try:
                if not os.path.exists(new_path):
                    safe_rename(file_path, new_path)
                    file_path = new_path
                    logging.debug("Renamed long filename: %s -> %s", filename, new_name)
            except Exception as exc:
                logging.error("Error renaming file: %s", exc)
                continue

        normalized = os.path.abspath(os.fspath(file_path))
        with processed_files_lock:
            if normalized not in processed_files:
                processed_files.add(normalized)
                pretext_queue.put(normalized)


def scan_extract_files(config, extract_queue) -> None:
    extract_suffix = str(config["EXTRACT_SUFFIX"]).lower()
    for filename in os.listdir(os.fspath(config["EXTRACT_WATCH_FOLDER"])):
        if filename.lower().endswith(extract_suffix):
            file_path = os.path.join(os.fspath(config["EXTRACT_WATCH_FOLDER"]), filename)
            if file_path not in extract_queue.queue:
                extract_queue.put(file_path)


def process_queue(config, queue, process, method_name, scan_files=None, shutdown_flag=None, *scan_args):
    intervals = config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)
    scan_seconds = intervals.get("SCAN_SECONDS", 60)
    next_scan = time.monotonic()
    sleep = shutdown_flag.wait if shutdown_flag is not None else time.sleep

    while shutdown_flag is None or not shutdown_flag.is_set():
        if scan_files and time.monotonic() >= next_scan:
            try:
                scan_files(*scan_args)
            except Exception as exc:
                logging.error("%s scan error: %s", method_name, exc)
            next_scan = time.monotonic() + scan_seconds

        if queue.empty():
            sleep(wait_seconds)
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
                    process(file_path)
                except LLMPermanentFailure as exc:
                    logging.error("Resilient Queue: OpenAI API permanent failure for file %s (model: %s): %s", exc.file_path, exc.model, exc.reason)
                except Exception as exc:
                    logging.error("%s queue error: %s", method_name, exc)

        except Exception as exc:
            logging.error("%s queue error: %s", method_name, exc)

        finally:
            if locked:
                with _file_locks_mutex:
                    _file_locks.pop(file_path, None)
                lock.release()

            queue.task_done()

        sleep(wait_seconds)


def process_text_pipeline(config, shutdown_flag):
    pretext_queue, extract_queue = Queue(), Queue()
    processed_files_global = set()
    processed_files_lock = threading.Lock()
    threads = {}

    if config["PIPELINES"]["PRETEXT"]:
        thread = threading.Thread(target=process_queue, args=(config, pretext_queue, lambda path: process_pretext_file(config, path, processed_files_global, processed_files_lock), "process_pretext", scan_pretext_files, shutdown_flag, config, pretext_queue, processed_files_global, processed_files_lock), daemon=True, name="TextPipeline-Pretext")
        thread.start()
        threads["TextPipeline-Pretext"] = thread

    if config["PIPELINES"]["EXTRACT"]:
        thread = threading.Thread(target=process_queue, args=(config, extract_queue, lambda path: process_extract_file(config, path), "process_extract", scan_extract_files, shutdown_flag, config, extract_queue), daemon=True, name="TextPipeline-Extract")
        thread.start()
        threads["TextPipeline-Extract"] = thread

    return threads