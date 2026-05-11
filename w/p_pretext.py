"""
p_pretext.py - Pretext processing for requested text files.

Responsibility:
Accept scan-requested pretext files, run LLM pretext generation, and write
pretext outputs and archives.

Pipelines:
- scan -> request -> queue -> read -> chunk -> llm -> merge -> write -> archive

Invariants:
- Queued pretext paths are normalized to absolute paths and de-duplicated.
- Pretext outputs are written as `_p.txt` files in the pretext watch folder.

Out of scope:
- Extract or premium extract workflows.
- Configuration construction and orchestrator wiring.
"""

import logging
import os
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_llm import call_llm
from .helper_files import read_file_with_encodings, release_text_file_permissions, safe_rename
from .helper_md import write_pretext_markdown
from .helper_text import chunk_text, intelligent_merge_chunks, sanitize_and_trim_filename


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
