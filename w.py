import os
import time
import whisper
import subprocess
import shutil
import logging
import warnings
from datetime import datetime
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler('script.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Configuration for audio processing
WATCH_FOLDER = r'C:\Users\KN\Desktop\Sync\Whisper'
DONE_FOLDER = r'C:\Users\KN\Desktop\YT1'
DESKTOP_FOLDER = r'C:\Users\KN\Desktop\Sync\Whisper'
NIGHT_FOLDER = os.path.join(WATCH_FOLDER, 'Night')
MODEL_SIZE = "large-v3-turbo"
SORT_ORDER = False

# Suppress Whisper warnings
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")
warnings.filterwarnings("ignore", category=FutureWarning, message=r"You are using `torch.load` with `weights_only=False`")

def find_audio_files_in_folder(path):
    return any(file.endswith(('.mp4', '.mp3', '.m4a')) for file in os.listdir(path))

def update_folder_path():
    current_hour = time.localtime().tm_hour
    is_night_time = current_hour >= 23 or current_hour < 5
    
    if find_audio_files_in_folder(WATCH_FOLDER):
        logging.info(f"Found audio files in root folder: {WATCH_FOLDER}")
        return WATCH_FOLDER
    elif is_night_time and os.path.exists(NIGHT_FOLDER) and find_audio_files_in_folder(NIGHT_FOLDER):
        logging.info(f"Found audio files in night folder: {NIGHT_FOLDER}")
        return NIGHT_FOLDER
    else:
        return None

def get_audio_files_sorted_by_size(folder_path):
    audio_files = [
        file for file in os.listdir(folder_path) if file.endswith(('.mp4', '.mp3', '.m4a'))
    ]
    audio_files.sort(key=lambda f: os.path.getsize(os.path.join(folder_path, f)), reverse=SORT_ORDER)
    return audio_files

def trim_filename(filename):
    """Trim filename to 60 characters while preserving extension."""
    name, ext = os.path.splitext(filename)
    if len(filename) > 60:
        return name[:60-len(ext)] + ext
    return filename

def convert_audio_to_wav(folder_path, audio_file):
    input_path = os.path.join(folder_path, audio_file)
    # Use trimmed filename for output
    trimmed_name = trim_filename(audio_file)
    output_file = trimmed_name.rsplit('.', 1)[0] + '.wav'
    output_path = os.path.join(folder_path, output_file)

    try:
        subprocess.run(
            ['ffmpeg', '-i', input_path, '-ac', '1', '-ar', '16000', output_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return output_path, trimmed_name
    except subprocess.CalledProcessError as e:
        logging.error(f"Error converting {audio_file}: {e}")
        return None, None

def move_files_to_done(audio_file_path, wav_file_path, process_time, done_folder_path):
    if wav_file_path and os.path.exists(wav_file_path):
        os.remove(wav_file_path)

    target_path = os.path.join(done_folder_path, os.path.basename(audio_file_path))
    if os.path.exists(target_path):
        os.remove(target_path)
    shutil.move(audio_file_path, target_path)
    logging.info(f"Processing completed in {process_time:.2f} seconds.")

class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

class AudioProcessor:
    def __init__(self, folder_path, model, done_folder_path):
        self.folder_path = folder_path
        self.model = model
        self.done_folder_path = done_folder_path
        self.supported_extensions = ('.mp4', '.mp3', '.m4a')

    def check_and_process_files(self):
        """Check folders and process files if found."""
        current_folder = update_folder_path()
        if current_folder:
            self.folder_path = current_folder
            self.process_and_transcribe_files()

    def process_and_transcribe_files(self):
        """Processes files in sorted size order and transcribes them."""
        while True:
            sorted_files = get_audio_files_sorted_by_size(self.folder_path)
            if not sorted_files:
                # Check root folder specifically when no files in current folder
                if find_audio_files_in_folder(WATCH_FOLDER) and self.folder_path != WATCH_FOLDER:
                    logging.info(f"Switching to root folder: {WATCH_FOLDER}")
                    self.folder_path = WATCH_FOLDER
                    continue  # Continue the loop with the root folder
                break  # Exit if no files in root or current folder

            file_name = sorted_files[0]
            file_path = os.path.join(self.folder_path, file_name)
            wav_file_path, trimmed_name = convert_audio_to_wav(self.folder_path, file_name)

            if wav_file_path:
                start_time = time.time()
                logging.info(f"Starting transcription of: {trimmed_name}")
                # Use trimmed name for transcription
                self.transcribe_and_handle_file(wav_file_path, file_path, start_time, trimmed_name)
                logging.info("Completed transcription.")
                
                # After transcription, first check root folder
                if find_audio_files_in_folder(WATCH_FOLDER):
                    logging.info(f"Found new files in root folder, switching to: {WATCH_FOLDER}")
                    self.folder_path = WATCH_FOLDER
                else:
                    # If no files in root, check other folders
                    new_folder = update_folder_path()
                    if new_folder:
                        self.folder_path = new_folder
                    else:
                        break  # No more files found in any folder
            else:
                logging.error(f"Failed to convert {file_path} to WAV format.")
                # Skip problematic file
                try:
                    problematic_file = os.path.join(self.done_folder_path, os.path.basename(file_path))
                    shutil.move(file_path, problematic_file)
                    logging.info(f"Moved problematic file to: {problematic_file}")
                except Exception as e:
                    logging.error(f"Error handling problematic file: {e}")

            time.sleep(1)

    def transcribe_and_handle_file(self, wav_file_path, original_file_path, start_time, trimmed_name):
        """Transcribes the file and moves it to 'done' folder."""
        logging.info("Processing transcription.")
        result = self.model.transcribe(wav_file_path)
        transcription_text = result["text"]
        logging.info("Transcription completed.")
        
        # Save transcription to a .txt file in the desktop folder using trimmed name
        txt_filename = trimmed_name.rsplit('.', 1)[0] + '.txt'
        txt_file_path = os.path.join(DESKTOP_FOLDER, txt_filename)
        
        with open(txt_file_path, 'w', encoding='utf-8') as f:
            f.write(transcription_text)

        # Move the original file to 'done' folder and delete the .wav file
        process_time = int(time.time() - start_time)
        move_files_to_done(original_file_path, wav_file_path, process_time, self.done_folder_path)

def main():
    logging.info("Starting audio transcription processor")
    
    # Create necessary folders
    os.makedirs(DONE_FOLDER, exist_ok=True)
    
    # Load Whisper model
    logging.info("Loading Whisper model...")
    model = whisper.load_model(MODEL_SIZE)
    
    # Initialize audio processor
    audio_processor = AudioProcessor(folder_path=WATCH_FOLDER, model=model, done_folder_path=DONE_FOLDER)
    
    try:
        while True:
            audio_processor.check_and_process_files()
            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Stopping processor...")
    finally:
        logging.info("Program terminated.")

if __name__ == "__main__":
    main() 
