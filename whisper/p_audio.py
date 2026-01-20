"""
Responsibility:
Scan configured audio folders, enqueue audio files, transcribe them via the
turbo service, and archive results along with temporary file cleanup.

Pipelines:
- scan -> enqueue -> convert -> transcribe -> write -> archive

Invariants:
- Transcriptions are written as UTF-8 text files in the configured output folder.
- Converted WAV files are removed after processing completes or fails.

Out of scope:
- Managing downstream text processing pipelines.
- Providing queue shutdown or cancellation controls.
"""

import os
import time
import subprocess
import shutil
import logging
import sys
from queue import Queue
from pathlib import Path

from utils_files import release_text_file_permissions
from utils_text import sanitize_and_trim_filename

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_whisper import get_service  # noqa: E402

SORT_ORDER = False  # Process smallest files first to reduce time-to-first-result.
DESKTOP_PATH = '/desktop'
#DESKTOP_PATH = '/mnt/c/Users/KN/Desktop'

audio_queue = Queue()


def find_audio_files_in_folder(path: str) -> bool:
    """
    Purpose:
    Determine whether a folder contains any supported audio files.
    Inputs:
    - path: Folder path to inspect.
    Outputs:
    - True when at least one supported audio file exists, otherwise False.
    Side effects:
    - Reads the filesystem.
    Failure modes:
    - Propagates exceptions from os.listdir.
    """
    if not os.path.exists(path):
        return False
    return any(
        fn.lower().endswith(('.mp4', '.mp3', '.m4a', '.ts', '.mkv')) for fn in os.listdir(path)
    )


def _iter_audio_watch_folders(config: dict) -> list[str]:
    """
    Purpose:
    Normalize audio watch folder configuration into a list of paths.
    Inputs:
    - config: Configuration mapping.
    Outputs:
    - List of folder paths.
    Side effects:
    - None.
    Failure modes:
    - None.
    """
    folders = config.get('AUDIO_WATCH_FOLDERS')
    if not folders:
        fallback = config.get('AUDIO_WATCH_FOLDER')
        folders = [fallback] if fallback else []
    elif isinstance(folders, (str, os.PathLike)):
        folders = [folders]
    return [os.fspath(folder) for folder in folders if folder]


def update_folder_path(config: dict) -> list[str]:
    """
    Purpose:
    Filter configured audio watch folders to those containing audio files.
    Inputs:
    - config: Configuration mapping.
    Outputs:
    - List of folders that currently contain audio files.
    Side effects:
    - Reads the filesystem.
    Failure modes:
    - Propagates exceptions from filesystem access.
    """
    available = []
    for folder in _iter_audio_watch_folders(config):
        if find_audio_files_in_folder(folder):
            available.append(folder)
    return available


def get_audio_files_sorted_by_size(folder_path: str) -> list[str]:
    """
    Purpose:
    List supported audio files in a folder, sorted by size.
    Inputs:
    - folder_path: Folder path to scan.
    Outputs:
    - List of filenames sorted by size.
    Side effects:
    - Reads the filesystem.
    Failure modes:
    - Propagates exceptions from filesystem access.
    """
    if not os.path.exists(folder_path):
        return []
    audio_files = [
        fn for fn in os.listdir(folder_path)
        if fn.lower().endswith(('.mp4', '.mp3', '.m4a', '.ts', '.mkv'))
    ]
    audio_files.sort(key=lambda f: os.path.getsize(os.path.join(folder_path, f)), reverse=SORT_ORDER)
    return audio_files


def convert_audio_to_wav(folder_path: str, audio_file: str) -> str | None:
    """
    Purpose:
    Convert an input audio file to mono 16kHz WAV using ffmpeg.
    Inputs:
    - folder_path: Folder containing the input file.
    - audio_file: Filename of the input file.
    Outputs:
    - Path to the converted WAV file, or None on failure.
    Side effects:
    - Invokes ffmpeg and writes a WAV file to disk.
    Failure modes:
    - Returns None when ffmpeg fails.
    """
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
    """
    Purpose:
    Remove temporary WAV files and move the original audio into the done folder.
    Inputs:
    - audio_file_path: Full path to the original audio file.
    - wav_file_path: Full path to the WAV file, if created.
    - process_time: Processing duration in seconds.
    - done_folder_path: Destination folder for archived audio files.
    - sanitized_filename: Target filename in the done folder.
    Outputs:
    - None.
    Side effects:
    - Deletes and moves files on disk and logs processing time.
    Failure modes:
    - Propagates exceptions from filesystem operations.
    """
    if wav_file_path and os.path.exists(wav_file_path):
        os.remove(wav_file_path)
    target = os.path.join(done_folder_path, sanitized_filename)
    if os.path.exists(target):
        os.remove(target)
    shutil.move(audio_file_path, target)
    logging.info(f'Audio processed in {process_time:.2f}s')


def scan_audio_files(config: dict) -> None:
    """
    Purpose:
    Scan watch folders and enqueue audio files not yet queued.
    Inputs:
    - config: Configuration mapping.
    Outputs:
    - None.
    Side effects:
    - Reads directories and enqueues items into the global audio_queue.
    Failure modes:
    - Propagates exceptions from filesystem access.
    """
    for current_folder in update_folder_path(config):
        for audio_file in get_audio_files_sorted_by_size(current_folder):
            file_path = os.path.join(current_folder, audio_file)
            if file_path not in (item[0] for item in list(audio_queue.queue)):
                audio_queue.put((file_path, current_folder))
                logging.info('Queued %s', audio_file)


def process_audio_file(file_path: str, folder_path: str, config: dict, done_folder_path: str) -> bool:
    """
    Purpose:
    Convert audio to WAV, transcribe it, write text output, and archive inputs.
    Inputs:
    - file_path: Full path to the original audio file.
    - folder_path: Folder containing the original audio file.
    - config: Configuration mapping.
    - done_folder_path: Destination folder for archived audio files.
    Outputs:
    - True on successful transcription and archival, otherwise False.
    Side effects:
    - Runs ffmpeg, moves files, writes text output, and logs errors.
    Failure modes:
    - Returns False on conversion or transcription failure.
    """
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


def process_audio_queue(config, *_queues, processing_lock, done_folder_path):
    """
    Purpose:
    Continuously scan for audio files and process the audio_queue.
    Inputs:
    - config: Configuration mapping.
    - _queues: Unused positional arguments for compatibility with callers.
    - processing_lock: Lock to serialize audio processing.
    - done_folder_path: Destination folder for archived audio files.
    Outputs:
    - None.
    Side effects:
    - Enqueues and processes audio files in an infinite loop with sleeps.
    Failure modes:
    - Logs errors and continues.
    """
    while True:
        try:
            scan_audio_files(config)
            if audio_queue.empty():
                time.sleep(60)
                continue

            file_path, folder_path = audio_queue.get()
            if not os.path.exists(file_path):
                audio_queue.task_done()
                continue

            with processing_lock:
                success = process_audio_file(file_path, folder_path, config, done_folder_path)
                audio_queue.task_done()

            if success:
                logging.info('Audio processed successfully')

        except Exception as exc:
            logging.error('Audio queue error: %s', exc)
            time.sleep(5)
