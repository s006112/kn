"""
p_audio.py

Responsibility:
Scan configured audio folders, enqueue audio files, transcribe them via the
configured Whisper service, and archive results along with temporary file cleanup.

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
from .helper_text import sanitize_and_trim_filename, short_log_name

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_whisper import get_service  # noqa: E402

AUDIO_EXTENSIONS = ('.mp4', '.mp3', '.m4a', '.ts', '.mkv')
DESKTOP_PATH = '/desktop'
#DESKTOP_PATH = '/mnt/c/Users/KN/Desktop'

def _iter_audio_files(config: dict) -> list[tuple[str, str]]:
    """Return supported audio files from configured watch folders."""
    folders = config.get('AUDIO_WATCH_FOLDERS')
    if not folders:
        fallback = config.get('AUDIO_WATCH_FOLDER')
        folders = [fallback] if fallback else []
    elif isinstance(folders, (str, os.PathLike)):
        folders = [folders]

    audio_files = []
    for folder in folders:
        if not folder:
            continue
        folder_path = os.fspath(folder)
        if not os.path.exists(folder_path):
            continue
        audio_files.extend(
            (folder_path, fn)
            for fn in os.listdir(folder_path)
            if fn.lower().endswith(AUDIO_EXTENSIONS)
        )
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
        release_text_file_permissions(output_path)
        return output_path
    except subprocess.CalledProcessError as exc:
        logging.error(f'ffmpeg failed on %s: %s', short_log_name(audio_file), exc)
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
    release_text_file_permissions(target)
    logging.info('Audio processed in %.2fs', process_time)


def scan_audio_files(config: dict, audio_queue: Queue) -> None:
    """Scan watch folders and enqueue audio files not already queued."""
    queued = {item[0] for item in list(audio_queue.queue)}
    for current_folder, audio_file in _iter_audio_files(config):
        file_path = os.path.join(current_folder, audio_file)
        if file_path in queued:
            continue
        audio_queue.put((file_path, current_folder))
        queued.add(file_path)
        logging.info('Queued %s', short_log_name(audio_file))


def process_audio_file(file_path: str, folder_path: str, config: dict, done_folder_path: str) -> bool:
    """Convert, transcribe, write, and archive one audio file."""
    base_name, ext = os.path.splitext(os.path.basename(file_path))
    sanitized = sanitize_and_trim_filename(base_name)
    wav_file = convert_audio_to_wav(folder_path, os.path.basename(file_path))
    if not wav_file:
        move_files_to_done(file_path, None, 0, done_folder_path, sanitized + ext)
        return False
    wav_file = os.path.abspath(wav_file)
    desktop_wav_path = os.path.abspath(os.path.join(DESKTOP_PATH, os.path.basename(wav_file)))
    if wav_file != desktop_wav_path:
        if os.path.exists(desktop_wav_path):
            os.remove(desktop_wav_path)
        shutil.move(wav_file, desktop_wav_path)
        release_text_file_permissions(desktop_wav_path)
        wav_file = desktop_wav_path
    start = time.time()
    try:
        text = get_service(config.get("WHISPER_MODEL")).transcribe_file(wav_file)
        txt_path = os.path.join(config['AUDIO_TRANSCRIBED_TXT_FOLDER'], sanitized + str(config["PRETEXT_SUFFIX"]).lower())
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(text)
        release_text_file_permissions(txt_path)
    except Exception as exc:
        logging.error('Transcription failed: %s', exc)
        if os.path.exists(wav_file):
            os.remove(wav_file)
        return False
    move_files_to_done(file_path, wav_file, time.time() - start, done_folder_path, sanitized + ext)
    logging.info('Finished %s', short_log_name(sanitized))
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
    wait = shutdown_flag.wait if shutdown_flag is not None else time.sleep
    while True:
        if shutdown_flag is not None and shutdown_flag.is_set():
            return
        try:
            item = audio_queue.get(timeout=wait_seconds) if shutdown_flag is not None else audio_queue.get()
        except Empty:
            if once:
                return
            continue
        try:
            file_path, folder_path = item
            if os.path.exists(file_path):
                with processing_lock:
                    process_audio_file(file_path, folder_path, config, done_folder_path)
        except Exception as exc:
            logging.error('Audio queue error: %s', exc)
            wait(wait_seconds)
        finally:
            audio_queue.task_done()
        if once:
            return


def process_audio_pipeline(config, audio_queue, audio_processing_lock, shutdown_flag) -> None:
    current_thread = threading.current_thread()
    current_thread.name = "AudioPipeline-GPU"
    intervals = config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)

    while not shutdown_flag.is_set():
        scan_audio_files(config, audio_queue)
        process_audio_queue(
            config,
            audio_queue,
            processing_lock=audio_processing_lock,
            done_folder_path=os.fspath(config["AUDIO_DONE_FOLDER"]),
            shutdown_flag=shutdown_flag,
            once=True,
            wait_seconds=wait_seconds,
        )
