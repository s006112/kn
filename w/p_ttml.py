"""
p_ttml.py

Responsibility
Converts TTML or plain subtitle files into pretext text files and archives the originals.

Used by:
* w/evaluation.py
* w/p.py

Pipelines:
- scanner -> ttml queue -> readiness -> conversion -> text_file -> archive
"""

import os
import shutil
import re
import logging
import threading
import time
from queue import Empty
from .helper_files import release_text_file_permissions
from .helper_text import sanitize_and_trim_filename, short_log_name
from xml.dom.minidom import parse


_ttml_locks = {}
_ttml_locks_mutex = threading.Lock()


def extract_text(node):
    """Recursively extract text content from an XML node tree."""
    text = ''
    if node.nodeType == node.TEXT_NODE and node.data.strip():
        text = node.data.strip() + '\n'
    for child in node.childNodes:
        text += extract_text(child)
    return text


def process_text(line):
    """Normalize subtitle text spacing while preserving Chinese text continuity."""
    if re.search(r'[\u4e00-\u9fa5]', line):
        return re.sub(r'\s+', '', line)
    return re.sub(r'\s+', ' ', line.strip())


def _is_file_ready(path, wait=1.0):
    """Return whether a file size remains stable across the wait interval."""
    size1 = os.path.getsize(path)
    time.sleep(wait)
    return size1 == os.path.getsize(path)


def process_ttml_pipeline(config, ttml_queue, shutdown_flag):
    ttml_watch_folder = os.path.abspath(os.fspath(config["TTML_WATCH_FOLDER"]))
    pretext_watch_folder = os.fspath(config["PRETEXT_WATCH_FOLDER"])
    original_folder = os.fspath(config["ORIGINAL_FOLDER"])
    intervals = config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)

    while not shutdown_flag.is_set():
        if os.path.exists(ttml_watch_folder):
            for filename in os.listdir(ttml_watch_folder):
                if filename.lower().endswith(".ttml"):
                    src = os.path.join(ttml_watch_folder, filename)
                    if src not in ttml_queue.queue:
                        ttml_queue.put(src)

        try:
            src = ttml_queue.get(timeout=wait_seconds)
        except Empty:
            continue

        try:
            src = os.path.abspath(os.fspath(src))
            if not os.path.exists(src):
                continue
            if (
                not src.lower().endswith(".ttml")
                or os.path.dirname(src) != ttml_watch_folder
            ):
                continue
            if not _is_file_ready(src, wait=wait_seconds):
                if src not in ttml_queue.queue:
                    ttml_queue.put(src)
                continue

            locked = False
            try:
                with _ttml_locks_mutex:
                    lock = _ttml_locks.setdefault(src, threading.Lock())
                locked = lock.acquire(blocking=False)
                if not locked:
                    if src not in ttml_queue.queue:
                        ttml_queue.put(src)
                    continue

                handle_ttml(
                    src,
                    pretext_watch_folder,
                    original_folder,
                    sanitize_and_trim_filename,
                    str(config["PRETEXT_SUFFIX"]),
                )
            except Exception as e:
                logging.error(
                    "TTML Pipeline: Error processing %s: %s",
                    os.path.basename(src),
                    e,
                )
            finally:
                if locked:
                    with _ttml_locks_mutex:
                        _ttml_locks.pop(src, None)
                    lock.release()

        except Exception as e:
            logging.error("TTML Pipeline: Error processing queued file: %s", e)
        finally:
            ttml_queue.task_done()


def handle_ttml(path, watch_folder, original_folder, sanitize_and_trim_filename, pretext_suffix: str):
    """Convert a TTML file to plain text and archive the original."""
    lock = path + '.processing'
    filename = os.path.basename(path)

    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        char_count = len(content)
        logging.info("TTML: Start %s (characters: %s)", short_log_name(filename), f"{char_count:,}")

        os.rename(path, lock)

        first = content.split('\n')[0] if content else ''
        content_length = len(content)

        base_name = sanitize_and_trim_filename(os.path.splitext(filename)[0])
        out_txt = os.path.join(watch_folder, base_name + pretext_suffix)

        if not first.lstrip().startswith('<'):
            with open(out_txt, 'w', encoding='utf-8') as f:
                f.write(content)
            output_length = content_length
        else:
            dom = parse(lock)
            raw_lines = extract_text(dom.documentElement).splitlines()
            lines = [process_text(l) for l in raw_lines if l.strip()]
            processed_content = ' '.join(lines)

            with open(out_txt, 'w', encoding='utf-8') as f:
                f.write(processed_content)
            output_length = len(processed_content)
        release_text_file_permissions(out_txt)

        output_filename = os.path.basename(out_txt)
        logging.info("TTML: Created %s (%s characters)", short_log_name(output_filename), f"{output_length:,}")

        archive_filename = base_name + '.ttml'
        archive_path = os.path.join(original_folder, archive_filename)
        shutil.move(lock, archive_path)
        release_text_file_permissions(archive_path)

        logging.info("TTML: Completed %s", short_log_name(output_filename))

    except Exception as e:
        logging.error("TTML: Error processing %s: %s", short_log_name(filename), e)
        if os.path.exists(lock):
            try:
                os.rename(lock, path)
            except Exception as restore_error:
                logging.error("TTML: Failed to restore file %s: %s", short_log_name(filename), restore_error)
