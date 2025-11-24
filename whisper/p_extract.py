import os
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from watchdog.events import FileSystemEventHandler

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils_files import (
    release_text_file_permissions,
    read_file_with_encodings,
)
from utils_llm import call_llm
from utils_md import (
    merge_to_markdown,
    create_or_find_note_for_base_name,
    find_most_recent_md_by_prefix,
)

class BaseExtractHandler(FileSystemEventHandler):
    def __init__(self, config, queue, watch_folder_key, model_keys):
        self.config = config
        self.queue = queue
        self.watch_folder = config[watch_folder_key]
        self.model_keys = model_keys
        self.processed_files = set()

    def _queue_file(self, file_path):
        cond = (
            os.path.abspath(os.path.dirname(file_path)) == os.path.abspath(self.watch_folder)
            and file_path.lower().endswith('_p.txt')
            and file_path not in self.processed_files
            and file_path not in list(self.queue.queue)
        )
        if not cond:
            return
        self.queue.put(file_path)
        self.processed_files.add(file_path)
        logging.info(f"{self.__class__.__name__}: Queued {os.path.basename(file_path)}")

    def on_created(self, event):
        if event.is_directory:
            return
        try:
            self._queue_file(event.src_path)
        except Exception as e:
            logging.error(f"Error in {self.__class__.__name__}.on_created: {e}")

def process(self, file_path, get_next_available_filename):
    filename = os.path.basename(file_path)
    logging.info(f"{self.__class__.__name__}: Start processing {filename}")
    base = filename[:-6] if filename.lower().endswith('_p.txt') else os.path.splitext(filename)[0]

    try:
        content, enc = read_file_with_encodings(file_path)
        payload = f"《{base}》\n{content}"

        # Decide (or create) the target Markdown once; merge incrementally after each success.
        md_path, link_name, md_is_new_seed = create_or_find_note_for_base_name(
            self.config, base, allow_existing=True
        )

        any_success = False
        any_failure = False

        for key in self.model_keys:
            model = self.config[key]
            try:
                # Run extraction for this model
                result = call_llm(
                    model=model,
                    system_prompt=self.config['EXTRACT_PROMPT'],
                    user_text=payload,
                    file_path=file_path,
                )

                # Save per-pass raw extract
                os.makedirs(self.config['EXTRACT_FOLDER'], exist_ok=True)
                save_path = get_next_available_filename(self.config['EXTRACT_FOLDER'], base, '_e')
                with open(save_path, 'w', encoding='utf-8') as f:
                    f.write(result)
                release_text_file_permissions(save_path)

                # Merge this pass immediately into MD
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
                logging.error(f"{self.__class__.__name__}: model {key} failed for {filename}: {e}")
                # Write a per-model error file (best-effort)
                try:
                    os.makedirs(self.config['EXTRACT_FOLDER'], exist_ok=True)
                    err_path = os.path.join(self.config['EXTRACT_FOLDER'], f"{base}_e.{key}.error.txt")
                    with open(err_path, 'w', encoding='utf-8') as ef:
                        ef.write(f"Model: {model} (key: {key})\nError: {e}\n")
                    release_text_file_permissions(err_path)
                except Exception as w:
                    logging.error(f"Write per-model error file failed: {w}")

        # Outcome policy:
        # - If ANY model failed -> whole job FAIL (but successful extracts are already merged above).
        # - Else (all succeeded) -> Success.
        if any_failure:
            raise RuntimeError("One or more extraction models failed")

        # All models succeeded (minimal change: override dest for premium)
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
        # Overall error file (best-effort)
        try:
            os.makedirs(self.config['EXTRACT_FOLDER'], exist_ok=True)
            err_path = os.path.join(self.config['EXTRACT_FOLDER'], base_nm + '_e.error.txt')
            with open(err_path, 'w', encoding='utf-8') as f:
                f.write(f"Error: {e}\n")
            release_text_file_permissions(err_path)
        except Exception as w:
            logging.error(f"Write error file failed: {w}")
        # Move source to Fail only if it still exists
        try:
            fail = self.config['FAIL_FOLDER']
            os.makedirs(fail, exist_ok=True)
            if os.path.exists(file_path):
                shutil.move(file_path, os.path.join(fail, filename))
                logging.info(f"Moved failed file to Fail folder: {filename}")
            else:
                logging.info(f"Fail move skipped; source missing: {filename}")
        except Exception as m:
            logging.error(f"Move to Fail folder failed: {m}")
        raise


class ExtractHandler(BaseExtractHandler):
    def __init__(self, config, queue):
        super().__init__(config, queue, 'WATCH_FOLDER',
                         ['GPT_MODEL_EXTRACT_1', 'GPT_MODEL_EXTRACT_2'])
    process_extract = process

class PremiumExtractHandler(BaseExtractHandler):
    def __init__(self, config, queue):
        super().__init__(config, queue, 'PREMIUM_WATCH_FOLDER',
                         ['GPT_MODEL_EXTRACT_3'])
    process_premium_extract = process
