import os
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEventHandler

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from p_context import PipelineContext
from utils_files import (
    release_text_file_permissions,
    read_file_with_encodings,
)
from utils_text import (
    chunk_text,
    intelligent_merge_chunks,
    sanitize_and_trim_filename,
)
from utils_llm import call_llm
from utils_md import write_pretext_markdown


class PretextHandler(FileSystemEventHandler):
    """Watch pretext folder events and request queueing via PipelineContext."""

    def __init__(self, ctx: PipelineContext):
        self.ctx = ctx
        self.watch_folder = os.path.abspath(os.fspath(ctx.config['WATCH_FOLDER']))

    def _handle_path(self, path: str) -> None:
        if not _is_pretext_candidate(path, self.watch_folder):
            return
        if request_pretext_processing(self.ctx, path):
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


class PretextProcessor:
    """Business logic for turning raw text into pretext outputs."""

    def __init__(self, ctx: PipelineContext):
        self.ctx = ctx

    def process_pretext(self, file_path, get_next_available_filename):  # signature kept for queue API
        process_pretext_file(self.ctx, file_path)


def request_pretext_processing(ctx: PipelineContext, file_path: str) -> bool:
    """Register and queue a pretext job once per file path."""
    normalized = os.path.abspath(os.fspath(file_path))
    with ctx.processed_files_lock:
        if normalized in ctx.processed_files_global:
            return False
        ctx.processed_files_global.add(normalized)
        ctx.pretext_queue.put(normalized)
        return True


def release_pretext_request(ctx: PipelineContext, file_path: str) -> None:
    normalized = os.path.abspath(os.fspath(file_path))
    with ctx.processed_files_lock:
        ctx.processed_files_global.discard(normalized)


def _is_pretext_candidate(path: Optional[str], watch_folder: str) -> bool:
    if not path:
        return False
    normalized = os.path.abspath(os.fspath(path))
    if os.path.dirname(normalized) != watch_folder:
        return False
    name = os.path.basename(normalized).lower()
    return name.endswith('.txt') and not name.endswith('_p.txt')


def process_pretext_file(ctx: PipelineContext, file_path: str) -> None:
    config = ctx.config
    normalized_path = os.path.abspath(os.fspath(file_path))
    try:
        os.makedirs(config['ORIGINAL_FOLDER'], exist_ok=True)
        if not os.path.exists(normalized_path):
            return

        original_filename = os.path.basename(normalized_path)
        base_name = sanitize_and_trim_filename(
            os.path.splitext(original_filename)[0]
        )
        archive_filename = f"{base_name}.txt"
        original_path = os.path.join(config['ORIGINAL_FOLDER'], archive_filename)

        content, encoding_used = read_file_with_encodings(normalized_path)
        char_count = len(content)
        logging.info(
            "Pretext: Start %s (characters: %s)",
            original_filename,
            f"{char_count:,}",
        )
        logging.debug(
            "File read successfully using %s encoding, content length: %s",
            encoding_used,
            f"{len(content):,}",
        )

        pretext_model = config['MODEL_PRETEXT']
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
                    system_prompt=config['PRETEXT_PROMPT'],
                    user_text=chunk,
                    file_path=normalized_path,
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
            all_results.insert(0, chunk_result)
            logging.debug(
                "Pretext: API call %d/%d successful, response length: %s",
                i,
                len(chunks),
                f"{len(chunk_result):,}",
            )
        all_results.reverse()
        pretext_result = intelligent_merge_chunks(all_results)
        if not pretext_result:
            raise ValueError("Empty combined response from OpenAI API")

        pretext_char_count = len(pretext_result)
        logging.info(
            "Pretext: Completed %s (%s : %s)",
            original_filename,
            pretext_model,
            f"{pretext_char_count:,}",
        )

        pretext_target_path = os.path.join(
            config['PRETEXT_WATCH_FOLDER'], f"{base_name}_p.txt"
        )
        with open(pretext_target_path, 'w', encoding='utf-8') as f:
            f.write(pretext_result)
        release_text_file_permissions(pretext_target_path)
        logging.info("Pretext: Created %s", os.path.basename(pretext_target_path))

        write_pretext_markdown(config, base_name, pretext_result)
        shutil.move(normalized_path, original_path)

    except Exception as exc:
        logging.error("Error processing file: %s", exc)
        if 'pretext_result' in locals():
            error_path = os.path.join(
                config['PRETEXT_WATCH_FOLDER'], f"{base_name}.error"
            )
            with open(error_path, 'w', encoding='utf-8') as f:
                f.write(f"Error: {exc}\nPartial response:\n{pretext_result}")
            release_text_file_permissions(error_path)
        raise
    finally:
        release_pretext_request(ctx, normalized_path)
