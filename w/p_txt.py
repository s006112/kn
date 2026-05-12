from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from queue import Queue, Empty

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

from helper.helper_llm import LLMPermanentFailure, call_llm
from .helper_files import get_next_available_filename, read_file_with_encodings, release_text_file_permissions, safe_rename
from .helper_md import create_or_find_note_for_base_name, merge_to_markdown, write_pretext_markdown
from .helper_text import chunk_text, intelligent_merge_chunks, sanitize_and_trim_filename, sanitize_filename, short_log_name


_file_locks = {}
_file_locks_mutex = threading.Lock()


def call_text_llm(config, model, system_prompt, user_text, file_path):
	intervals = config["INTERVALS"]
	return call_llm(model=model, system_prompt=system_prompt, user_text=user_text, file_path=file_path, max_retries=intervals.get("LLM_MAX_RETRIES", 2), timeout=intervals.get("LLM_TIMEOUT_SECONDS", 90), retry_delay=intervals.get("LLM_RETRY_DELAY_SECONDS", 10))


def write_text_file(path, content):
	with open(path, "w", encoding="utf-8") as f:
		f.write(content)
	release_text_file_permissions(path)
	return path


def save_extract_result(config, base_name, model, result, md_path=None, link_name=None, md_is_new=False, merge_label=None):
	os.makedirs(config["EXTRACT_FOLDER"], exist_ok=True)
	save_path = write_text_file(get_next_available_filename(config["EXTRACT_FOLDER"], base_name, f"_{sanitize_filename(model)}"), result)
	if md_path:
		merge_to_markdown(md_path, [result], "", [merge_label or f"{model} "], whisper_md_path=os.path.join(config["OBSIDIAN_SYNC_FOLDER"], "Whisper 000000.md"), whisper_link_name=link_name or Path(md_path).stem, md_is_new=md_is_new)
	return save_path


def save_pipeline_error(config, stage, base_name, error, *, filename=None, model=None, file_path=None):
	logging.error("Pipeline error | stage=%s file=%s base=%s model=%s error=%s", stage, short_log_name(filename) if filename else "", short_log_name(base_name), model or "", error)
	if not file_path or not os.path.exists(file_path):
		return None

	error_path = os.path.join(os.path.dirname(os.path.abspath(os.fspath(file_path))), f"{base_name}.error")
	try:
		os.replace(file_path, error_path)
		release_text_file_permissions(error_path)
		logging.info("Marked failed file as error: %s", short_log_name(error_path))
		return error_path
	except Exception as rename_error:
		logging.error("Pipeline error rename failed | source=%s target=%s error=%s", short_log_name(file_path), short_log_name(error_path), rename_error)
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

		content, encoding_used = read_file_with_encodings(normalized_path)
		filename_log = short_log_name(original_filename)
		logging.info("Pretext: Start %s (characters: %s)", filename_log, f"{len(content):,}")
		logging.debug("File read successfully using %s encoding, content length: %s", encoding_used, f"{len(content):,}")

		chunks, all_results = chunk_text(content), []
		logging.info("Pretext: Split into %d chunks", len(chunks))

		for i, chunk in enumerate(chunks, 1):
			logging.debug("Pretext: API call %d/%d for %s using %s", i, len(chunks), filename_log, pretext_model)
			chunk_result = call_text_llm(config, pretext_model, config["PRETEXT_PROMPT"], chunk, normalized_path)
			if not chunk_result: raise ValueError(f"Empty response from OpenAI API for chunk {i}")
			all_results.append(chunk_result)
			logging.debug("Pretext: API call %d/%d successful, response length: %s", i, len(chunks), f"{len(chunk_result):,}")

		pretext_result = intelligent_merge_chunks(all_results)
		if not pretext_result:
			raise ValueError("Empty combined response from OpenAI API")

		logging.info("Pretext: Completed %s (%s : %s)", filename_log, pretext_model, f"{len(pretext_result):,}")
		pretext_target_path = write_text_file(os.path.join(config["PRETEXT_WATCH_FOLDER"], f"{base_name}{config['EXTRACT_SUFFIX']}"), pretext_result)
		logging.info("Pretext: Created %s", short_log_name(pretext_target_path))

		write_pretext_markdown(config, base_name, pretext_result)
		shutil.move(normalized_path, os.path.join(config["ORIGINAL_FOLDER"], f"{base_name}.txt"))

	except Exception as exc:
		save_pipeline_error(config, "pretext", base_name, exc, filename=original_filename, model=pretext_model, file_path=normalized_path)
		raise
	finally:
		with processed_files_lock:
			processed_files.discard(normalized_path)


def process_extract_file(config, file_path):
	filename = os.path.basename(file_path)
	extract_suffix = str(config["EXTRACT_SUFFIX"]).lower()
	base = filename[: -len(extract_suffix)] if filename.lower().endswith(extract_suffix) else os.path.splitext(filename)[0]
	filename_log = short_log_name(filename)

	try:
		logging.info("Extract: Start %s", filename_log)
		content, _ = read_file_with_encodings(file_path)

		classifier_result = (call_text_llm(config, config["MODEL_PRETEXT"], config["CLASSIFIER_PROMPT"], content, file_path) or "").strip().upper()
		route = "CORE" if classifier_result == "CORE" else "OTHER"
		logging.info("Extract: |%s| for %s", route, filename_log)

		payload = f"《{base}》\n{content}"
		md_path, link_name, md_is_new_seed = create_or_find_note_for_base_name(config, base, allow_existing=True)

		extract_models = list(config.get("MODEL_EXTRACT_MATRIX", {}).get("EXTRACT_WATCH_FOLDER", []))
		distill_model = (config.get("MODEL_DISTILL") or "").strip()
		if route == "OTHER":
			extract_models = extract_models[:1]
			distill_model = None

		extract_count = 0
		for model in extract_models:
			if not model:
				logging.info("Extract: Skipping model entry (not configured)")
				continue

			result = call_text_llm(config, model, config["EXTRACT_PROMPT"], payload, file_path)
			save_extract_result(config, base, model, result, md_path, link_name, md_is_new_seed and extract_count == 0)
			extract_count += 1
			logging.info("Extract: %s (%s : %s)", filename_log, model, f"{len(result):,}")

		if distill_model:
			logging.info("Extract: Model %s for %s", distill_model, filename_log)
			run_distillation(config, base_name=base, md_path=md_path)
			logging.info("Extract: Completed for %s", filename_log)

		os.makedirs(config["PRETEXT_DONE_FOLDER"], exist_ok=True)
		shutil.move(file_path, os.path.join(config["PRETEXT_DONE_FOLDER"], filename))

	except Exception as exc:
		if isinstance(exc, FileNotFoundError) or not os.path.exists(file_path):
			return
		save_pipeline_error(config, "extract", base, exc, filename=filename, model="extract", file_path=file_path)
		raise


def collect_extracts(extract_folder: str, base_name: str, pretext_suffix: str):
	if not os.path.isdir(extract_folder):
		return []

	extracts, errors = [], []
	prefix, suffix = f"{base_name}_", pretext_suffix.lower()

	for fname in sorted(fn for fn in os.listdir(extract_folder) if fn.startswith(prefix) and fn.lower().endswith(suffix)):
		path = os.path.join(extract_folder, fname)
		try:
			content, _ = read_file_with_encodings(path)
			extracts.append((fname, content, path))
		except Exception as exc:
			logging.error("Distillation: failed to read extract %s: %s", short_log_name(fname), exc)
			errors.append(fname)

	if errors:
		raise RuntimeError(f"Failed to read extract files for {base_name}: {', '.join(errors)}")

	return extracts


def run_distillation(config, base_name: str, md_path: str | None = None) -> str | None:
	extract_folder = os.fspath(config["EXTRACT_FOLDER"])
	distill_model = (config.get("MODEL_DISTILL") or "").strip()

	if not distill_model:
		logging.info("Distillation: MODEL_DISTILL not configured, skipping for %s", short_log_name(base_name))
		return None

	extracts = collect_extracts(extract_folder, base_name, str(config["PRETEXT_SUFFIX"]))
	if not extracts:
		logging.info("Distillation: No extracts found for %s, skipping", short_log_name(base_name))
		return None

	payload = [f"《{base_name}》", "Below are outputs from multiple expert extraction models for the same source. Please distill them into one final, coherent result according to the system instructions."]
	for i, (fname, content, path) in enumerate(extracts, 1):
		payload += [f"--- Source {i}: {fname} ---", content.strip()]

	logging.info("Distillation: Start %s %s (%d inputs)", short_log_name(base_name), distill_model, len(extracts))
	distilled = call_text_llm(config, distill_model, config["DISTILL_PROMPT"], "\n\n".join(payload), extracts[0][2])
	save_path = save_extract_result(config, base_name, distill_model, distilled, md_path, Path(md_path).stem if md_path else None, False, f"{distill_model} distilled")

	logging.info("Distillation: Completed %s -> %s", short_log_name(base_name), short_log_name(save_path))
	return save_path


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
					logging.debug("Renamed long filename: %s -> %s", short_log_name(filename), short_log_name(new_name))
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

	while shutdown_flag is None or not shutdown_flag.is_set():
		if scan_files and time.monotonic() >= next_scan:
			try:
				scan_files(*scan_args)
			except Exception as exc:
				logging.error("%s scan error: %s", method_name, exc)
			next_scan = time.monotonic() + scan_seconds

		try:
			file_path = queue.get(timeout=wait_seconds)
		except Empty:
			continue

		locked = False
		try:
			with _file_locks_mutex:
				lock = _file_locks.setdefault(file_path, threading.Lock())

			locked = lock.acquire(blocking=False)
			if not locked:
				queue.put(file_path)
				continue

			try:
				process(file_path)
			except LLMPermanentFailure as exc:
				logging.error("Resilient Queue: OpenAI API permanent failure for file %s (model: %s): %s", short_log_name(exc.file_path), exc.model, exc.reason)

		except Exception as exc:
			logging.error("%s queue error: %s", method_name, exc)

		finally:
			if locked:
				with _file_locks_mutex:
					_file_locks.pop(file_path, None)
				lock.release()
			queue.task_done()


def _start_text_thread(threads, name, config, queue, process, method_name, scan_files, shutdown_flag, *scan_args):
	thread = threads[name] = threading.Thread(target=process_queue, args=(config, queue, process, method_name, scan_files, shutdown_flag, *scan_args), daemon=True, name=name)
	thread.start()


def process_text_pipeline(config, shutdown_flag):
	pretext_queue, extract_queue = Queue(), Queue()
	processed_files_global = set()
	processed_files_lock = threading.Lock()
	threads = {}

	if config["PIPELINES"]["PRETEXT"]: _start_text_thread(threads, "TextPipeline-Pretext", config, pretext_queue, lambda path: process_pretext_file(config, path, processed_files_global, processed_files_lock), "process_pretext", scan_pretext_files, shutdown_flag, config, pretext_queue, processed_files_global, processed_files_lock)
	if config["PIPELINES"]["EXTRACT"]: _start_text_thread(threads, "TextPipeline-Extract", config, extract_queue, lambda path: process_extract_file(config, path), "process_extract", scan_extract_files, shutdown_flag, config, extract_queue)

	return threads