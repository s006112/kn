"""
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
from helper.utils_llm import call_llm
from utils_md import write_pretext_markdown


class v(FileSystemEventHandler):
    """Watch pretext folder events and request queueing via PipelineContext."""

    def __init__(self, ctx: PipelineContext):
        """
        Purpose:
        Initialize a handler bound to the pipeline context and watch folder.
        Inputs:
        - ctx: PipelineContext with config and queues.
        Outputs:
        - None.
        Side effects:
        - Stores context and resolves the absolute watch folder path.
        Failure modes:
        - Propagates exceptions from path normalization.
        """
        self.ctx = ctx
        self.watch_folder = os.path.abspath(os.fspath(ctx.config['PRETEXT_WATCH_FOLDER']))

    def _handle_path(self, path: str) -> None:
        """
        Purpose:
        Validate a path and enqueue it for pretext processing if eligible.
        Inputs:
        - path: File path to evaluate.
        Outputs:
        - None.
        Side effects:
        - Enqueues the file and logs when queued.
        Failure modes:
        - Propagates exceptions from queue operations.
        """
        if not _is_pretext_candidate(path, self.watch_folder):
            return
        if request_pretext_processing(self.ctx, path):
            logging.info("Pretext: Queued %s", os.path.basename(path))

    def on_created(self, event):
        """
        Purpose:
        Respond to file creation events from watchdog.
        Inputs:
        - event: Filesystem event containing the source path.
        Outputs:
        - None.
        Side effects:
        - May enqueue the created file.
        Failure modes:
        - Logs and suppresses exceptions.
        """
        if event.is_directory:
            return
        try:
            self._handle_path(event.src_path)
        except Exception as exc:
            logging.error("Error in PretextHandler.on_created: %s", exc)

    def on_moved(self, event):
        """
        Purpose:
        Respond to file move events from watchdog.
        Inputs:
        - event: Filesystem event containing the destination path.
        Outputs:
        - None.
        Side effects:
        - May enqueue the moved file.
        Failure modes:
        - Logs and suppresses exceptions.
        """
        if event.is_directory:
            return
        try:
            self._handle_path(event.dest_path)
        except Exception as exc:
            logging.error("Error in PretextHandler.on_moved: %s", exc)


class PretextProcessor:
    """Business logic for turning raw text into pretext outputs."""

    def __init__(self, ctx: PipelineContext):
        """
        Purpose:
        Initialize the processor with the pipeline context.
        Inputs:
        - ctx: PipelineContext with config and queues.
        Outputs:
        - None.
        Side effects:
        - Stores the context reference.
        Failure modes:
        - None.
        """
        self.ctx = ctx

    def process_pretext(self, file_path, get_next_available_filename):  # signature kept for queue API
        """
        Purpose:
        Process a queued pretext file using the shared pipeline context.
        Inputs:
        - file_path: Path to the file to process.
        - get_next_available_filename: Unused compatibility parameter.
        Outputs:
        - None.
        Side effects:
        - Reads, transcribes, and writes pretext output files.
        Failure modes:
        - Propagates exceptions from process_pretext_file.
        """
        process_pretext_file(self.ctx, file_path)


def request_pretext_processing(ctx: PipelineContext, file_path: str) -> bool:
    """
    Purpose:
    Register and enqueue a pretext job once per normalized file path.
    Inputs:
    - ctx: PipelineContext with queue and tracking set.
    - file_path: Path to the source file.
    Outputs:
    - True when the file is newly queued, otherwise False.
    Side effects:
    - Adds to processed_files_global and enqueues the path.
    Failure modes:
    - Propagates exceptions from queue operations.
    """
    normalized = os.path.abspath(os.fspath(file_path))
    with ctx.processed_files_lock:
        if normalized in ctx.processed_files_global:
            return False
        ctx.processed_files_global.add(normalized)
        ctx.pretext_queue.put(normalized)
        return True


def release_pretext_request(ctx: PipelineContext, file_path: str) -> None:
    """
    Purpose:
    Remove a normalized path from the global processed set.
    Inputs:
    - ctx: PipelineContext with tracking set.
    - file_path: Path to remove from tracking.
    Outputs:
    - None.
    Side effects:
    - Updates processed_files_global under lock.
    Failure modes:
    - None.
    """
    normalized = os.path.abspath(os.fspath(file_path))
    with ctx.processed_files_lock:
        ctx.processed_files_global.discard(normalized)


def _is_pretext_candidate(path: Optional[str], watch_folder: str) -> bool:
    """
    Purpose:
    Decide if a path is a pretext candidate within the watch folder.
    Inputs:
    - path: Path to validate.
    - watch_folder: Absolute watch folder path.
    Outputs:
    - True when the path is a non-pretext .txt file in the watch folder.
    Side effects:
    - None.
    Failure modes:
    - None.
    """
    if not path:
        return False
    normalized = os.path.abspath(os.fspath(path))
    if os.path.dirname(normalized) != watch_folder:
        return False
    name = os.path.basename(normalized).lower()
    return name.endswith('.txt') and not name.endswith('_p.txt')


def process_pretext_file(ctx: PipelineContext, file_path: str) -> None:
    """
    Purpose:
    Generate pretext output from a text file and archive the original.
    Inputs:
    - ctx: PipelineContext with configuration and queues.
    - file_path: Path to the source text file.
    Outputs:
    - None.
    Side effects:
    - Reads source text, calls LLM, writes output and markdown, moves files.
    Failure modes:
    - Logs and re-raises exceptions; writes .error file on partial results.
    """
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
