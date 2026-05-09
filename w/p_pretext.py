"""
p_pretext.py - Pretext processing for text files in the watch folder.

Responsibility:
Watch pretext folders, queue new text files, run LLM pretext generation, and
write pretext outputs and archives.

Pipelines:
- watch -> queue -> read -> chunk -> llm -> merge -> write -> archive

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
from typing import Optional

from watchdog.events import FileSystemEventHandler

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_llm import call_llm
from utils_files import read_file_with_encodings, release_text_file_permissions
from utils_md import write_pretext_markdown
from utils_text import chunk_text, intelligent_merge_chunks, sanitize_and_trim_filename


class PretextHandler(FileSystemEventHandler):
    """Watch pretext folder events and queue eligible files."""

    def __init__(self, config, queue, processed_files, processed_files_lock):
        self.config = config
        self.queue = queue
        self.processed_files = processed_files
        self.processed_files_lock = processed_files_lock
        self.watch_folder = os.path.abspath(os.fspath(config["PRETEXT_WATCH_FOLDER"]))

    def _handle_path(self, path: str) -> None:
        pretext_suffix = str(self.config["PRETEXT_SUFFIX"]).lower()
        extract_suffixes = tuple(
            str(s).lower() for s in self.config["EXTRACT_SUFFIX"] if str(s)
        )
        if not _is_pretext_candidate(
            path,
            self.watch_folder,
            pretext_suffix=pretext_suffix,
            extract_suffixes=extract_suffixes,
        ):
            return
        if request_pretext_processing(
            self.queue,
            self.processed_files,
            self.processed_files_lock,
            path,
        ):
            logging.info("Pretext: Queued %s", os.path.basename(path))

    def on_created(self, event):
        if event.is_directory:
            return
        try:
            self._handle_path(event.src_path)
        except Exception as exc:
            logging.error("Error in PretextHandler.on_created: %s", exc)

    def on_moved(self, event):
        if event.is_directory:
            return
        try:
            self._handle_path(event.dest_path)
        except Exception as exc:
            logging.error("Error in PretextHandler.on_moved: %s", exc)


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


def _is_pretext_candidate(
    path: Optional[str],
    watch_folder: str,
    *,
    pretext_suffix: str,
    extract_suffixes,
) -> bool:
    if not path:
        return False
    normalized = os.path.abspath(os.fspath(path))
    if os.path.dirname(normalized) != watch_folder:
        return False
    name = os.path.basename(normalized).lower()
    return name.endswith(pretext_suffix.lower()) and not any(
        name.endswith(s) for s in extract_suffixes
    )


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