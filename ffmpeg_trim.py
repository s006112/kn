# ffmpeg_trim.py

import os
import re
import subprocess
import shutil
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Configuration
WATCH_FOLDER = Path("/desktop")
SUPPORTED_EXTENSIONS = {".mp4", ".mp3", ".ts", ".mpeg", ".mkv", ".mov", ".avi"}
# Pattern: base_name + space + start(2-6 digits)-end(2-6 digits) + extension
FILENAME_PATTERN = re.compile(r"^(.*)\s(\d{2,6})-(\d{2,6})$")

def format_time(token):
    """
    Converts numeric tokens (2-6 digits) to HH:MM:SS format.
    Rules:
    2 digits: SS -> 00:00:SS
    3 digits: MSS -> 00:0M:SS
    4 digits: MMSS -> 00:MM:SS
    5 digits: HMMSS -> 0H:MM:SS
    6 digits: HHMMSS -> HH:MM:SS
    """
    padded = token.zfill(6)
    hh = padded[0:2]
    mm = padded[2:4]
    ss = padded[4:6]
    return f"{hh}:{mm}:{ss}"

def move_to_trash(file_path):
    """
    Moves the file to the user's standard desktop trash folder.
    """
    trash_dir = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Trash" / "files"
    try:
        if not trash_dir.exists():
            trash_dir.mkdir(parents=True, exist_ok=True)
        
        dest_path = trash_dir / file_path.name
        # Handle filename collisions in trash
        if dest_path.exists():
            dest_path = trash_dir / f"{int(time.time())}_{file_path.name}"
            
        shutil.move(str(file_path), str(dest_path))
    except Exception as e:
        print(f"Error moving {file_path.name} to trash: {e}")

def process_file(file_path):
    print(f"[status] Detected file: {file_path.name}")

    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        print(f"[status] Skipped unsupported file: {file_path.name}")
        return

    stem = file_path.stem
    match = FILENAME_PATTERN.match(stem)
    if not match:
        print(f"[status] Skipped file without trim pattern: {file_path.name}")
        return

    base_name, start_token, end_token = match.groups()
    
    # Parse timestamps
    start_time = format_time(start_token)
    end_time = format_time(end_token)
    
    # Define output (defaulting to .mp4 as per user preference)
    output_filename = f"{base_name}.mp4"
    output_path = file_path.parent / output_filename

    # Guard: Do not overwrite existing files or re-process output
    if output_path.exists():
        print(f"[status] Skipped because output already exists: {output_path.name}")
        return

    # Use a size-focused re-encode: CRF plus a slower preset compresses better
    # than a simple bitrate target for most real-world clips.
    cmd = [
        "ffmpeg", "-y", "-i", str(file_path),
        "-ss", start_time,
        "-to", end_time,
        "-vf", "scale=640:360",  # Resize video to 640x360.
        "-c:v", "libx264",  # Encode video with H.264.
        "-crf", "28",  # CRF quality range is typically 18-32; smaller means better quality and larger files.
        "-preset", "slow",  # Spend more CPU time for better compression.
        "-c:a", "aac",  # Encode audio as AAC for MP4 compatibility.
        "-b:a", "96K",  # Limit audio bitrate to keep audio smaller.
        "-movflags", "+faststart",  # Move MP4 metadata to the front for faster playback start.
        str(output_path)
    ]

    try:
        print(
            f"[status] Trimming {file_path.name} -> {output_path.name} "
            f"({start_time} to {end_time})"
        )
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and output_path.exists():
            move_to_trash(file_path)
            print(f"[status] Completed: {output_path.name}")
        else:
            print(f"[status] FFmpeg failed for {file_path.name}: {result.stderr.strip()}")
    except Exception as e:
        print(f"Failed to trim {file_path.name}: {e}")

def scan_existing_files():
    print(f"[status] Scanning existing files in: {WATCH_FOLDER}")
    for file_path in sorted(WATCH_FOLDER.iterdir()):
        if file_path.is_file():
            process_file(file_path)

class MediaWatcherHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            self._process(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._process(event.dest_path)

    def _process(self, path):
        file_path = Path(path)
        # Settle time to ensure file is fully written/copied
        print(f"[status] Waiting for file to settle: {file_path.name}")
        time.sleep(2)
        process_file(file_path)

def main():
    if not WATCH_FOLDER.exists():
        print(f"Directory not found: {WATCH_FOLDER}")
        return

    scan_existing_files()

    event_handler = MediaWatcherHandler()
    observer = Observer()
    observer.schedule(event_handler, str(WATCH_FOLDER), recursive=False)
    
    print(f"Watcher active on: {WATCH_FOLDER}")
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()
