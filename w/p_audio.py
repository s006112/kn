"""
p_audio.py

Responsibility:
Scan configured audio folders, enqueue audio files, transcribe them via the
turbo service, and archive results along with temporary file cleanup.

Pipelines:
- scan -> enqueue -> convert -> transcribe -> write -> archive

"""

import os
import time
import subprocess
import shutil
import logging
import sys
import threading
from queue import Empty, Queue
from pathlib import Path

from .helper_files import release_text_file_permissions
from .helper_text import sanitize_and_trim_filename

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_whisper import get_service  # noqa: E402
#from helper.helper_cohere import get_service  # noqa: E402


SORT_ORDER = False  # Process smallest files first to reduce time-to-first-result.
DESKTOP_PATH = '/desktop'
#DESKTOP_PATH = '/mnt/c/Users/KN/Desktop'

def find_audio_files_in_folder(path: str) -> bool:
    """Return whether a folder contains supported audio files."""
    if not os.path.exists(path):
        return False
    return any(
        fn.lower().endswith(('.mp4', '.mp3', '.m4a', '.ts', '.mkv')) for fn in os.listdir(path)
    )


def _iter_audio_watch_folders(config: dict) -> list[str]:
    """Return configured audio watch folders as path strings."""
    folders = config.get('AUDIO_WATCH_FOLDERS')
    if not folders:
        fallback = config.get('AUDIO_WATCH_FOLDER')
        folders = [fallback] if fallback else []
    elif isinstance(folders, (str, os.PathLike)):
        folders = [folders]
    return [os.fspath(folder) for folder in folders if folder]


def update_folder_path(config: dict) -> list[str]:
    """Return configured audio watch folders that currently contain audio files."""
    available = []
    for folder in _iter_audio_watch_folders(config):
        if find_audio_files_in_folder(folder):
            available.append(folder)
    return available


def get_audio_files_sorted_by_size(folder_path: str) -> list[str]:
    """Return supported audio filenames sorted by file size."""
    if not os.path.exists(folder_path):
        return []
    audio_files = [
        fn for fn in os.listdir(folder_path)
        if fn.lower().endswith(('.mp4', '.mp3', '.m4a', '.ts', '.mkv'))
    ]
    audio_files.sort(key=lambda f: os.path.getsize(os.path.join(folder_path, f)), reverse=SORT_ORDER)
    return audio_files


def convert_audio_to_wav(folder_path: str, audio_file: str) -> str | None:
    """Convert an audio file to mono 16kHz WAV using ffmpeg."""
    input_path = os.path.join(folder_path, audio_file)
    output_path = os.path.join(folder_path, audio_file.rsplit('.', 1)[0] + '.wav')
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-loglevel', 'error', '-i', input_path, '-ac', '1', '-ar', '16000', output_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return output_path
    except subprocess.CalledProcessError as exc:
        logging.error(f'ffmpeg failed on {audio_file}: {exc}')
        return None


def move_files_to_done(
    audio_file_path: str,
    wav_file_path: str | None,
    process_time: float,
    done_folder_path: str,
    sanitized_filename: str,
) -> None:
    """Remove temporary WAV output and move original audio to the done folder."""
    if wav_file_path and os.path.exists(wav_file_path):
        os.remove(wav_file_path)
    target = os.path.join(done_folder_path, sanitized_filename)
    if os.path.exists(target):
        os.remove(target)
    shutil.move(audio_file_path, target)
    logging.info(f'Audio processed in {process_time:.2f}s')


def scan_audio_files(config: dict, audio_queue: Queue) -> None:
    """Scan watch folders and enqueue audio files not already queued."""
    for current_folder in update_folder_path(config):
        for audio_file in get_audio_files_sorted_by_size(current_folder):
            file_path = os.path.join(current_folder, audio_file)
            if file_path not in (item[0] for item in list(audio_queue.queue)):
                audio_queue.put((file_path, current_folder))
                logging.info('Queued %s', audio_file)


def process_audio_file(file_path: str, folder_path: str, config: dict, done_folder_path: str) -> bool:
    """Convert, transcribe, write, and archive one audio file."""
    base_name, ext = os.path.splitext(os.path.basename(file_path))
    sanitized = sanitize_and_trim_filename(base_name)

    wav_file = convert_audio_to_wav(folder_path, os.path.basename(file_path))
    if not wav_file:
        # Avoid repeatedly retrying files that cannot be converted.
        move_files_to_done(file_path, None, 0, done_folder_path, sanitized + ext)
        return False
    desktop_wav_path = os.path.join(DESKTOP_PATH, os.path.basename(wav_file))
    source_wav_path = os.path.abspath(wav_file)
    desktop_wav_path = os.path.abspath(desktop_wav_path)
    if source_wav_path != desktop_wav_path:
        if os.path.exists(desktop_wav_path):
            os.remove(desktop_wav_path)
        shutil.move(source_wav_path, desktop_wav_path)
        wav_file = desktop_wav_path
    else:
        wav_file = source_wav_path

    try:
        start = time.time()
        service = get_service()
        text = service.transcribe_file(wav_file)
    except Exception as exc:
        logging.error('Transcription failed: %s', exc)
        if os.path.exists(wav_file):
            os.remove(wav_file)
        return False

    pretext_suffix = str(config["PRETEXT_SUFFIX"]).lower()
    txt_path = os.path.join(config['AUDIO_TRANSCRIBED_TXT_FOLDER'], sanitized + pretext_suffix)
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(text)
    release_text_file_permissions(txt_path)

    move_files_to_done(file_path, wav_file, time.time() - start, done_folder_path, sanitized + ext)
    logging.info('Finished %s', sanitized)
    return True


def process_audio_queue(
    config,
    audio_queue: Queue,
    *,
    processing_lock,
    done_folder_path,
    shutdown_flag=None,
    once: bool = False,
    wait_seconds=None,
):
    """Continuously wait for and process queued audio files."""
    if wait_seconds is None:
        intervals = config.get("INTERVALS", {})
        wait_seconds = intervals.get("WAIT_SECONDS", 1.0)
    while shutdown_flag is None or not shutdown_flag.is_set():
        queued_item = None
        try:
            if shutdown_flag is None:
                queued_item = audio_queue.get()
            else:
                queued_item = audio_queue.get(timeout=wait_seconds)
            file_path, folder_path = queued_item
            if not os.path.exists(file_path):
                continue

            with processing_lock:
                success = process_audio_file(file_path, folder_path, config, done_folder_path)

            if success:
                logging.info('Audio processed successfully')

        except Empty:
            if once:
                return
            continue
        except Exception as exc:
            logging.error('Audio queue error: %s', exc)
            if shutdown_flag is not None:
                shutdown_flag.wait(wait_seconds)
            else:
                time.sleep(wait_seconds)
        finally:
            if queued_item is not None:
                audio_queue.task_done()
        if once:
            return


def process_audio_pipeline(ctx) -> None:
    current_thread = threading.current_thread()
    current_thread.name = "AudioPipeline-GPU"
    intervals = ctx.config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)

    while not ctx.shutdown_flag.is_set():
        scan_audio_files(ctx.config, ctx.audio_queue)
        process_audio_queue(
            ctx.config,
            ctx.audio_queue,
            processing_lock=ctx.audio_processing_lock,
            done_folder_path=os.fspath(ctx.config["AUDIO_DONE_FOLDER"]),
            shutdown_flag=ctx.shutdown_flag,
            once=True,
            wait_seconds=wait_seconds,
        )
