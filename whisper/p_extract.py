"""
Responsibility:
Watch extract folders, run model-based extraction for pretext files, merge results
into markdown, and archive or fail files with distillation when configured.

Pipelines:
- watch -> queue -> read -> extract -> merge -> distill -> archive

Invariants:
- Extract jobs only accept `_p.txt` files in the configured watch folders.
- Per-model extracts are written before markdown merges are attempted.

Out of scope:
- Pretext generation and audio transcription workflows.
- Orchestrator wiring and queue thread management.
"""

import os
import logging
import shutil
import sys
from pathlib import Path
from watchdog.events import FileSystemEventHandler

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from p_distill import run_distillation
from utils_files import (
    release_text_file_permissions,
    read_file_with_encodings,
)
from helper.utils_llm import call_llm
from utils_md import (
    merge_to_markdown,
    create_or_find_note_for_base_name,
)
from utils_text import sanitize_filename

class BaseExtractHandler(FileSystemEventHandler):
    """
    Base handler for extract pipelines with shared queueing and processing logic.
    """

    def __init__(self, config, queue, watch_folder_key, model_names, *, enable_distillation=True):
        """
        Purpose:
        Initialize a handler for a specific extract watch folder and model list.
        Inputs:
        - config: Configuration mapping.
        - queue: Queue to receive file paths.
        - watch_folder_key: Config key for the watch folder.
        - model_names: Iterable of model names to run.
        - enable_distillation: Whether to run distillation after success.
        Outputs:
        - None.
        Side effects:
        - Stores configuration, queue, and model list.
        Failure modes:
        - Propagates exceptions from configuration access.
        """
        self.config = config
        self.queue = queue
        self.watch_folder = config[watch_folder_key]
        self.models = list(model_names or [])
        self.processed_files = set()
        self.enable_distillation = enable_distillation

    def finalize_success(self, filename: str, base_name: str, md_path: str | None) -> None:
        """
        Purpose:
        Optionally run distillation after successful extraction.
        Inputs:
        - filename: Source filename for logging.
        - base_name: Base name used for distillation.
        - md_path: Markdown path used by distillation.
        Outputs:
        - None.
        Side effects:
        - Logs and runs distillation when configured.
        Failure modes:
        - Propagates exceptions from distillation utilities.
        """
        if getattr(self, "enable_distillation", True):
            distill_model = (self.config.get("MODEL_DISTILL") or "").strip()
            if distill_model:
                logging.info(
                    f"{self.__class__.__name__}: Distillation with {distill_model} for {filename}"
                )
                distill_path = run_distillation(
                    self.config,
                    base_name=base_name,
                    md_path=md_path,
                )
                logging.info(
                    f"{self.__class__.__name__}: Distillation completed for {filename} ({distill_path or 'skipped'})"
                )
            else:
                logging.info(
                    f"{self.__class__.__name__}: Distillation skipped for {filename} (MODEL_DISTILL disabled)"
                )
        else:
            logging.info(
                f"{self.__class__.__name__}: Distillation bypassed for {filename} (premium pipeline)"
            )

    def _queue_file(self, file_path):
        """
        Purpose:
        Queue a file path for processing if it is eligible and not already tracked.
        Inputs:
        - file_path: Path to the candidate file.
        Outputs:
        - None.
        Side effects:
        - Adds the file to the queue and processed set.
        Failure modes:
        - Propagates exceptions from queue operations.
        """
        extract_suffixes = tuple(
            str(s).lower() for s in self.config["EXTRACT_SUFFIX"] if str(s)
        )

        cond = (
            os.path.abspath(os.path.dirname(file_path)) == os.path.abspath(self.watch_folder)
            and any(file_path.lower().endswith(s) for s in extract_suffixes)
            and file_path not in self.processed_files
            and file_path not in list(self.queue.queue)
        )
        if not cond:
            return
        self.queue.put(file_path)
        self.processed_files.add(file_path)
        logging.info(f"{self.__class__.__name__}: Queued {os.path.basename(file_path)}")

    def on_created(self, event):
        """
        Purpose:
        Respond to watchdog file creation events by queueing eligible files.
        Inputs:
        - event: Filesystem event with the source path.
        Outputs:
        - None.
        Side effects:
        - Enqueues the file when eligible.
        Failure modes:
        - Logs and suppresses exceptions.
        """
        if event.is_directory:
            return
        try:
            self._queue_file(event.src_path)
        except Exception as e:
            logging.error(f"Error in {self.__class__.__name__}.on_created: {e}")

def process(self, file_path, get_next_available_filename):
    """
    Purpose:
    Run extraction across configured models, merge results, and archive the source.
    Inputs:
    - file_path: Path to the pretext source file.
    - get_next_available_filename: Callable to generate output filenames.
    Outputs:
    - None.
    Side effects:
    - Calls LLMs, writes extract and markdown files, moves sources on success/failure.
    Failure modes:
    - Raises exceptions for extraction failures or filesystem errors.
    """
    filename = os.path.basename(file_path)
    logging.info(f"{self.__class__.__name__}: Start processing {filename}")
    extract_suffixes = tuple(
        str(s).lower() for s in self.config["EXTRACT_SUFFIX"] if str(s)
    )

    filename_lower = filename.lower()
    matched_suffix = next((s for s in sorted(extract_suffixes, key=len, reverse=True) if filename_lower.endswith(s)), None)
    base = filename[: -len(matched_suffix)] if matched_suffix else os.path.splitext(filename)[0]

    try:
        content, enc = read_file_with_encodings(file_path)
        payload = f"《{base}》\n{content}"

        # Avoid repeated note selection so merges stay consistent across models.
        md_path, link_name, md_is_new_seed = create_or_find_note_for_base_name(
            self.config, base, allow_existing=True
        )

        any_success = False
        any_failure = False

        for model in self.models:
            if not model:
                logging.info(
                    f"{self.__class__.__name__}: Skipping model entry (not configured)"
                )
                continue
            try:
                # Keep model runs isolated to allow partial results and per-model errors.
                result = call_llm(
                    model=model,
                    system_prompt=self.config['EXTRACT_PROMPT'],
                    user_text=payload,
                    file_path=file_path,
                )

                # Preserve raw output before merging to allow later audits.
                os.makedirs(self.config['EXTRACT_FOLDER'], exist_ok=True)
                model_suffix = f"_{sanitize_filename(model)}"
                save_path = get_next_available_filename(self.config['EXTRACT_FOLDER'], base, model_suffix)
                with open(save_path, 'w', encoding='utf-8') as f:
                    f.write(result)
                release_text_file_permissions(save_path)

                # Merge immediately to keep the markdown up to date after each success.
                label = f"{model} "
                merge_to_markdown(
                    md_path, [result], "", [label],
                    whisper_md_path=os.path.join(self.config['OBSIDIAN_SYNC_FOLDER'], 'Whisper 000000.md'),
                    whisper_link_name=link_name,
                    md_is_new=(md_is_new_seed and not any_success)
                )
                any_success = True
                result_chars = len(result)
                logging.info(
                    f"{self.__class__.__name__}: {filename} "
                    f"({model} : {result_chars:,})"
                )

            except Exception as e:
                any_failure = True
                logging.error(f"{self.__class__.__name__}: model {model} failed for {filename}: {e}")
                # Write a per-model error file (best-effort)
                try:
                    os.makedirs(self.config['PRETEXT_WATCH_FOLDER'], exist_ok=True)
                    err_key = sanitize_filename(model) or "unknown_model"
                    err_path = os.path.join(
                        self.config['PRETEXT_WATCH_FOLDER'],
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

        self.finalize_success(filename=filename, base_name=base, md_path=md_path)

        # Premium pipeline archives to a different folder to keep outputs segregated.
        dest_dir = self.config['PRETEXT_DONE_FOLDER']
        if os.path.abspath(os.path.dirname(file_path)) == os.path.abspath(self.config['PREMIUM_WATCH_FOLDER']):
            dest_dir = self.config['ARCHIVE_FOLDER']
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(file_path, os.path.join(dest_dir, filename))
        return

    except Exception as e:
        # If the source file is already moved/missing (common with duplicate queue events),
        # treat it as benign and exit quietly.
        if isinstance(e, FileNotFoundError) or not os.path.exists(file_path):
            logging.info(f"{self.__class__.__name__}: Skipping stale item (source missing): {filename}")
            return

        logging.error(f"Error processing {filename}: {e}")
        base_nm = os.path.splitext(filename)[0]
        # Preserve a top-level error marker for troubleshooting.
        try:
            os.makedirs(self.config['PRETEXT_WATCH_FOLDER'], exist_ok=True)
            err_path = os.path.join(
                self.config['PRETEXT_WATCH_FOLDER'], f"{base_nm}.error"
            )
            with open(err_path, 'w', encoding='utf-8') as f:
                f.write(f"Error: {e}\n")
            release_text_file_permissions(err_path)
        except Exception as w:
            logging.error(f"Write error file failed: {w}")
        # Only move if the source still exists to avoid duplicate errors.
        try:
            os.makedirs(self.config['FAIL_FOLDER'], exist_ok=True)
            if os.path.exists(file_path):
                shutil.move(file_path, os.path.join(self.config['FAIL_FOLDER'], filename))
                logging.info(f"Moved failed file to Fail folder: {filename}")
            else:
                logging.info(f"Fail move skipped; source missing: {filename}")
        except Exception as m:
            logging.error(f"Move to Fail folder failed: {m}")
        raise


class ExtractHandler(BaseExtractHandler):
    def __init__(self, config, queue):
        """
        Purpose:
        Initialize the standard extract handler using the extract model matrix.
        Inputs:
        - config: Configuration mapping.
        - queue: Queue to receive file paths.
        Outputs:
        - None.
        Side effects:
        - Configures models and enables distillation.
        Failure modes:
        - Propagates exceptions from configuration access.
        """
        model_matrix = config.get('MODEL_EXTRACT_MATRIX', {})
        models = model_matrix.get('EXTRACT_WATCH_FOLDER', [])
        super().__init__(config, queue, 'EXTRACT_WATCH_FOLDER', models, enable_distillation=True)
    process_extract = process

class PremiumExtractHandler(BaseExtractHandler):
    def __init__(self, config, queue):
        """
        Purpose:
        Initialize the premium extract handler using the premium model matrix.
        Inputs:
        - config: Configuration mapping.
        - queue: Queue to receive file paths.
        Outputs:
        - None.
        Side effects:
        - Configures models and disables distillation.
        Failure modes:
        - Propagates exceptions from configuration access.
        """
        model_matrix = config.get('MODEL_EXTRACT_MATRIX', {})
        models = model_matrix.get('PREMIUM_WATCH_FOLDER', [])
        super().__init__(config, queue, 'PREMIUM_WATCH_FOLDER', models, enable_distillation=False)

    def finalize_success(self, filename: str, base_name: str, md_path: str | None) -> None:
        """
        Purpose:
        Override distillation to do nothing for premium extracts.
        Inputs:
        - filename: Source filename for logging.
        - base_name: Base name for extraction output.
        - md_path: Markdown path, unused.
        Outputs:
        - None.
        Side effects:
        - None.
        Failure modes:
        - None.
        """
        return

    process_premium_extract = process
