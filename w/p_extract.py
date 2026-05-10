"""
p_extract.py: Process scan-queued extract files and merge model outputs

Responsibility:
Run model-based extraction for scan-queued pretext files, merge results into
markdown, and archive or fail files with distillation when configured.

Pipelines:
- scan -> queue -> read -> extract -> merge -> distill -> archive

"""

import os
import logging
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from .p_distill import run_distillation
from .helper_files import (
    release_text_file_permissions,
    read_file_with_encodings,
)
from helper.helper_llm import call_llm
from .helper_md import (
    merge_to_markdown,
    create_or_find_note_for_base_name,
)
from .helper_text import sanitize_filename

class BaseExtractProcessor:
    """Base processor for extract pipelines."""

    def __init__(self, config, model_names, *, enable_distillation=True):
        """Initialize an extract processor for one model list."""
        self.config = config
        self.models = list(model_names or [])
        self.enable_distillation = enable_distillation

    def finalize_success(self, filename: str, base_name: str, md_path: str | None) -> None:
        """Run distillation after successful extraction when configured."""
        if getattr(self, "enable_distillation", True):
            distill_model = (self.config.get("MODEL_DISTILL") or "").strip()
            if distill_model:
                logging.info(
                    f"Extract: Model {distill_model} for {filename}"
                )
                distill_path = run_distillation(
                    self.config,
                    base_name=base_name,
                    md_path=md_path,
                )
                logging.info(
                    f"Extract: Completed for {filename} ({distill_path or 'skipped'})"
                )
            else:
                logging.info(
                    f"Extract: Skipped for {filename} (MODEL_DISTILL disabled)"
                )
        else:
            logging.info(
                f"Extract: Bypassed for {filename} (premium pipeline)"
            )

def process(self, file_path, get_next_available_filename):
    """Run configured extraction models, merge results, and archive the source."""
    filename = os.path.basename(file_path)
    logging.info(f"Extract: Start processing {filename}")
    extract_suffixes = tuple(
        str(s).lower() for s in self.config["EXTRACT_SUFFIX"] if str(s)
    )

    filename_lower = filename.lower()
    matched_suffix = next((s for s in sorted(extract_suffixes, key=len, reverse=True) if filename_lower.endswith(s)), None)
    base = filename[: -len(matched_suffix)] if matched_suffix else os.path.splitext(filename)[0]

    try:
        content, _ = read_file_with_encodings(file_path)
        payload = f"《{base}》\n{content}"
        intervals = self.config.get("INTERVALS", {})

        # Avoid repeated note selection so merges stay consistent across models.
        # For markdown-source triggers, write into the existing note with the same filename
        # (no dated note naming), and still ensure it is linked from Whisper 000000.md.
        if matched_suffix == ".md":
            md_path = os.path.join(self.config["OBSIDIAN_SYNC_FOLDER"], filename)
            link_name = os.path.splitext(filename)[0]
            md_is_new_seed = True
        else:
            md_path, link_name, md_is_new_seed = create_or_find_note_for_base_name(
                self.config, base, allow_existing=True
            )

        any_success = False
        any_failure = False

        for model in self.models:
            if not model:
                logging.info(
                    f"Extract: Skipping model entry (not configured)"
                )
                continue
            try:
                # Keep model runs isolated to allow partial results and per-model errors.
                result = call_llm(
                    model=model,
                    system_prompt=self.config['EXTRACT_PROMPT'],
                    user_text=payload,
                    file_path=file_path,
                    max_retries=intervals.get("LLM_MAX_RETRIES", 2),
                    timeout=intervals.get("LLM_TIMEOUT_SECONDS", 90),
                    retry_delay=intervals.get("LLM_RETRY_DELAY_SECONDS", 10),
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
                    f"Extract: {filename} "
                    f"({model} : {result_chars:,})"
                )

            except Exception as e:
                any_failure = True
                logging.error(f"Extract: Model {model} failed for {filename}: {e}")
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
            logging.info(f"Extract: Skipping stale item (source missing): {filename}")
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


class ExtractProcessor(BaseExtractProcessor):
    def __init__(self, config):
        """Initialize the standard extract processor with distillation enabled."""
        model_matrix = config.get('MODEL_EXTRACT_MATRIX', {})
        models = model_matrix.get('EXTRACT_WATCH_FOLDER', [])
        super().__init__(config, models, enable_distillation=True)
    process_extract = process

class PremiumExtractProcessor(BaseExtractProcessor):
    def __init__(self, config):
        """Initialize the premium extract processor with distillation disabled."""
        model_matrix = config.get('MODEL_EXTRACT_MATRIX', {})
        models = model_matrix.get('PREMIUM_WATCH_FOLDER', [])
        super().__init__(config, models, enable_distillation=False)

    def finalize_success(self, filename: str, base_name: str, md_path: str | None) -> None:
        """Skip distillation for premium extracts."""
        return

    process_premium_extract = process
