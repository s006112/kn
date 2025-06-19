import os
import shutil
import re
import time
import openai
import logging
import warnings
import threading
import requests
import sys
from datetime import datetime
from dotenv import load_dotenv
from queue import Queue
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from xml.dom.minidom import parse


# Configuration for text chunking
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 20

# Configuration for file name trimming
TRIMMED_LENGTH = 60

# GPT_MODEL_PRETEXT = 'gpt-4.1-nano'
GPT_MODEL_PRETEXT = 'gpt-4.1-mini'
#GPT_MODEL_EXTRACT = 'gpt-4.1-mini'
#GPT_MODEL_EXTRACT = 'gpt-4o'
GPT_MODEL_EXTRACT = 'o4-mini'
#GPT_MODEL_EXTRACT = 'o1'
PROMPT_PRETEXT = 'prompt_pretext.txt'
PROMPT_EXTRACT = 'prompt_extract.txt'

# Configure OpenAI API settings
openai.timeout = 60  # Default timeout in seconds
openai.max_retries = 4  # Default retry attempts

# Call the OpenAI API with retry logic
def call_openai_with_retry(client, model, messages, max_retries=3):
    wait_time = 10
    
    for attempt in range(max_retries):
        try:
            params = {
                "model": model,
                "messages": messages,
                "temperature": 0.2 if model == GPT_MODEL_PRETEXT else 1.0,
                "timeout": openai.timeout
            }
            if model == "gpt-4o":
                params["max_tokens"] = 16384
            else:
                params["max_completion_tokens"] = 32768

            response = client.chat.completions.create(**params)
            return response.choices[0].message.content
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            
            wait_time = min(wait_time * 2, 15)
            logging.warning(f"API call failed (attempt {attempt + 1}): {str(e)}")
            time.sleep(wait_time)

# Load environment variables from .env file
load_dotenv()

# Configure logging with UTF-8 encoding
class UTFStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            stream.buffer.write(msg.encode('utf-8'))
            stream.buffer.write(self.terminator.encode('utf-8'))
            self.flush()
        except Exception:
            self.handleError(record)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler('script.log', encoding='utf-8'),
        UTFStreamHandler(sys.stdout)
    ]
)

# Configuration for text processing
WATCH_FOLDER = r'C:\Users\KN\Desktop\Sync\Whisper'
ORIGINAL_FOLDER = os.path.join(WATCH_FOLDER, 'Archive\Raw')
PRETEXT_TARGET_FOLDER = WATCH_FOLDER
EXTRACT_FOLDER = os.path.join(WATCH_FOLDER, 'Archive\Extract')
PRETEXT_FOLDER = os.path.join(WATCH_FOLDER, 'Archive')
OBSIDIAN_SYNC_FOLDER = r'C:\Users\KN\iCloudDrive\iCloud~md~obsidian\OB Whisper'

# Create queues for text processing
pretext_queue = Queue()
extract_queue = Queue()

# Global lock to ensure only one file is processed at a time
processing_lock = threading.Lock()

# Get OpenAI API key
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise ValueError("OpenAI API key not found. Please set OPENAI_API_KEY in your environment variables or .env file.")

# Read prompts from a file and return as a string
def read_prompt_file(filename):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        prompt_path = os.path.join(script_dir, filename)
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        logging.error(f"Error loading {filename}: {str(e)}")
        raise ValueError(f"Failed to load {filename}. Ensure the file exists in the script directory.")

PRETEXT_PROMPT = read_prompt_file(PROMPT_PRETEXT)
EXTRACT_PROMPT = read_prompt_file(PROMPT_EXTRACT)

# Sanitize a filename by replacing problematic symbols
def sanitize_filename(name):
    # Replace problematic symbols for markdown and filesystem, including fullwidth vertical bar
    for ch in ['#', '[', ']', '`', '/', '\\', '?', '*', '<', '>', '|']:
    # for ch in ['#', '[', ']', '`', '/', '\\', '?', '*', '<', '>', '|', '：', ':', '｜']:
        name = name.replace(ch, '_')
    return name

# Sanitize a filename by replacing problematic symbols and trim to a safe length
def sanitize_and_trim_filename(filename):
    filename = sanitize_filename(filename)
    try:
        name, ext = os.path.splitext(filename)
        if len(name) > TRIMMED_LENGTH:
            return name[:TRIMMED_LENGTH] + ext
        return filename
    except Exception as e:
        logging.error(f"Error trimming filename '{filename}': {str(e)}")
        return filename

# Get the next available filename in a directory with a given suffix
def get_next_available_filename(base_path, base_name, suffix='_e'):
    initial_path = os.path.join(base_path, f"{base_name}{suffix}.txt")
    if not os.path.exists(initial_path):
        return initial_path
    counter = 1
    while True:
        numbered_path = os.path.join(base_path, f"{base_name}{suffix}_{counter}.txt")
        if not os.path.exists(numbered_path):
            return numbered_path
        counter += 1

def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split text into chunks with overlap and optimized chunk sizes.
    
    Instead of using fixed chunk sizes that might leave a small remainder,
    this function calculates optimal chunk sizes to evenly distribute the text.
    """
    text_length = len(text)
    
    # Calculate number of chunks needed based on target chunk size
    n_chunks = (text_length + chunk_size - 1) // chunk_size  # Ceiling division
    
    # If text is smaller than chunk size, return as single chunk
    if n_chunks <= 1:
        return [text]
    
    # Calculate optimal chunk size to evenly distribute text
    optimal_chunk_size = text_length // n_chunks
    # Ensure optimal size isn't too small
    if optimal_chunk_size < (chunk_size * 0.5):  # If optimal size is less than 50% of target
        n_chunks = max(1, (text_length + chunk_size * 0.5 - 1) // (chunk_size * 0.5))
        optimal_chunk_size = text_length // n_chunks
    
    chunks = []
    start = 0
    
    for i in range(n_chunks):
        # For last chunk, just take all remaining text
        if i == n_chunks - 1:
            chunks.append(text[start:])
            break
            
        end = start + optimal_chunk_size
        # Add overlap for all chunks except the last one
        if i < n_chunks - 1:
            end += overlap
            
        chunks.append(text[start:end])
        # Move start position, accounting for overlap
        start = end - overlap
    
    # Log chunk distribution info
    chunk_sizes = [len(chunk) for chunk in chunks]
    logging.info(f"Text length: {text_length}, Chunks: {n_chunks}, "
                f"Optimal size: {optimal_chunk_size}, "
                f"Actual sizes: {chunk_sizes}")
    
    return chunks

# --- TTML Conversion Functions ---

# Extract all text content recursively from an XML node
def extract_text(node):
    text = ''
    if node.nodeType == node.TEXT_NODE and node.data.strip():
        text = node.data.strip() + '\n'
    for child in node.childNodes:
        text += extract_text(child)
    return text

# Normalize whitespace and remove spaces for Chinese text
def process_text(line):
    if re.search(r'[\u4e00-\u9fa5]', line):
        return re.sub(r'\s+', '', line)
    return re.sub(r'\s+', ' ', line.strip())

# Check if a file is ready by verifying its size is stable
def is_file_ready(path, wait=1.0):
    size1 = os.path.getsize(path)
    time.sleep(wait)
    return size1 == os.path.getsize(path)

# Convert a .ttml file to .txt and archive the original
def handle_ttml(path):
    lock = path + '.processing'
    try:
        os.rename(path, lock)
        with open(lock, 'r', encoding='utf-8', errors='replace') as f:
            first = f.readline()
            f.seek(0)
            content = f.read()
        filename = os.path.basename(path)
        sanitized_filename = sanitize_and_trim_filename(filename)
        base, _ = os.path.splitext(sanitized_filename)
        out_txt = os.path.join(WATCH_FOLDER, base + '.txt')
        if not first.lstrip().startswith('<'):
            with open(out_txt, 'w', encoding='utf-8') as f:
                f.write(content)
        else:
            dom = parse(lock)
            raw_lines = extract_text(dom.documentElement).splitlines()
            lines = [process_text(l) for l in raw_lines if l.strip()]
            with open(out_txt, 'w', encoding='utf-8') as f:
                f.write(' '.join(lines))
        archive_path = os.path.join(ORIGINAL_FOLDER, sanitized_filename)
        shutil.move(lock, archive_path)
        logging.info(f"TTML processed: {path} → {out_txt}, archived as {archive_path}")
    except Exception as e:
        logging.error(f"Error processing TTML {path}: {e}")
        if os.path.exists(lock):
            os.rename(lock, path)

# Text Processing Classes
class PretextHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
            
        try:
            # Check if the file is in the root folder and is a .txt file (not _p.txt)
            file_path = event.src_path
            if (os.path.dirname(file_path) == WATCH_FOLDER and 
                file_path.lower().endswith('.txt') and
                not os.path.basename(file_path).lower().endswith('_p.txt')):
                
                # Check if filename needs trimming
                filename = os.path.basename(file_path)
                try:
                    filename.encode('ascii')
                except UnicodeEncodeError:
                    logging.info(f"Processing non-ASCII filename: {filename}")
                
                new_filename = sanitize_and_trim_filename(filename)
                new_path = os.path.join(WATCH_FOLDER, new_filename)
                try:
                    if not os.path.exists(new_path):
                        safe_rename(file_path, new_path)
                        logging.info(f"Renamed long filename: {filename} -> {new_filename}")
                        file_path = new_path
                    else:
                        logging.warning(f"Cannot rename {filename} to {new_filename} as target already exists. Using original name.")
                except Exception as e:
                    logging.error(f"Error renaming file {filename}: {str(e)}")
                    # Continue with original filename if rename fails
                
                # Add to queue
                pretext_queue.put(file_path)
                logging.info(f"Added file to pretext queue: {file_path}")
        except Exception as e:
            logging.error(f"Error in PretextHandler.on_created: {str(e)}")

    def process_pretext(self, file_path):
        logging.info(f"Processing file: {file_path}")
        
        try:
            os.makedirs(ORIGINAL_FOLDER, exist_ok=True)

            if not os.path.exists(file_path):
                logging.error(f"File not found: {file_path}")
                return
            
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            original_path = os.path.join(ORIGINAL_FOLDER, os.path.basename(file_path))
            sanitized_base_name = sanitize_filename(base_name)

            # Read input file
            content = None
            for encoding in ['utf-8', 'gbk', 'gb2312', 'gb18030', 'big5']:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        content = f.read()
                        logging.info(f"Input text: {len(content):,} characters")
                        break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                raise ValueError(f"Unable to read file: {file_path}")

            # Process with OpenAI in chunks
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            chunks = chunk_text(content)
            logging.info(f"Split text into {len(chunks)} chunks")
            
            # Process each chunk and combine results
            all_results = []
            for i, chunk in enumerate(chunks, 1):
                logging.info(f"Processing chunk {i}/{len(chunks)} ({len(chunk):,} characters)")
                chunk_result = process_text_with_openai(client, GPT_MODEL_PRETEXT, PRETEXT_PROMPT, chunk)
                if chunk_result:
                    all_results.insert(0, chunk_result)  # Change append to insert(0) to maintain order
                else:
                    raise ValueError(f"Empty response from OpenAI API for chunk {i}")
            
            # Combine all results in correct order
            all_results.reverse()  # Reverse to get correct order
            pretext_result = intelligent_merge_chunks(all_results)
            
            if not pretext_result:
                raise ValueError("Empty combined response from OpenAI API")

            # Save result to pretext target folder
            os.makedirs(PRETEXT_TARGET_FOLDER, exist_ok=True)
            pretext_target_path = get_next_available_filename(PRETEXT_TARGET_FOLDER, sanitized_base_name, '_p')
            with open(pretext_target_path, 'w', encoding='utf-8') as f:
                f.write(pretext_result)
            logging.info(f"Output text: {len(pretext_result):,} characters")

            # Create markdown file in Obsidian sync folder
            os.makedirs(OBSIDIAN_SYNC_FOLDER, exist_ok=True)
            md_base_name = sanitized_base_name  # no _p or _e, sanitized
            datecode = datetime.now().strftime('%y%m%d')
            md_filename = f"{md_base_name}_{datecode}.md"
            md_path = os.path.join(OBSIDIAN_SYNC_FOLDER, md_filename)
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(pretext_result)
            # Add link to Whisper.md as the first new line
            whisper_md_path = os.path.join(OBSIDIAN_SYNC_FOLDER, 'Whisper.md')
            link_code = f"[[{md_base_name}_{datecode}]]\n"
            try:
                if os.path.exists(whisper_md_path):
                    with open(whisper_md_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    if lines:
                        lines.insert(1, link_code)
                    else:
                        lines = [link_code]
                    with open(whisper_md_path, 'w', encoding='utf-8') as f:
                        f.writelines(lines)
                else:
                    with open(whisper_md_path, 'w', encoding='utf-8') as f:
                        f.write(link_code)
            except Exception as e:
                logging.error(f"Error updating Whisper.md: {str(e)}")

            # Move original file
            shutil.move(file_path, original_path)
            logging.info(f"Processed and saved to: {pretext_target_path}")

        except Exception as e:
            logging.error(f"Error processing file: {str(e)}")
            if 'pretext_result' in locals():
                with open(pretext_target_path + ".error.txt", 'w', encoding='utf-8') as f:
                    f.write(f"Error: {str(e)}\nPartial response:\n{pretext_result}")

class ExtractHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
            
        try:
            # Check if the file is in the root folder and ends with _p.txt
            file_path = event.src_path
            if (os.path.dirname(file_path) == WATCH_FOLDER and 
                file_path.lower().endswith('_p.txt')):
                
                # Check if filename needs trimming (excluding _p.txt part)
                filename = os.path.basename(file_path)
                try:
                    filename.encode('ascii')
                except UnicodeEncodeError:
                    logging.info(f"Processing non-ASCII filename: {filename}")
                
                base_name = filename.replace('_p.txt', '')
                new_filename = sanitize_and_trim_filename(f"{base_name}_p.txt")
                new_path = os.path.join(WATCH_FOLDER, new_filename)
                try:
                    if not os.path.exists(new_path):
                        safe_rename(file_path, new_path)
                        logging.info(f"Renamed long filename: {filename} -> {new_filename}")
                        file_path = new_path
                    else:
                        logging.warning(f"Cannot rename {filename} to {new_filename} as target already exists. Using original name.")
                except Exception as e:
                    logging.error(f"Error renaming file {filename}: {str(e)}")
                    # Continue with original filename if rename fails
                
                # Add to queue
                extract_queue.put(file_path)
                logging.info(f"Added file to extract queue: {file_path}")
        except Exception as e:
            logging.error(f"Error in ExtractHandler.on_created: {str(e)}")

    def process_extract(self, file_path):
        logging.info(f"Processing pretext file: {file_path}")

        try:
            base_name = os.path.splitext(os.path.basename(file_path))[0].replace('_p', '')
            original_path = os.path.join(ORIGINAL_FOLDER, os.path.basename(file_path))

            # Read pretext file
            content = None
            for encoding in ['utf-8', 'gbk', 'gb2312', 'gb18030', 'big5']:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        content = f.read()
                        logging.info(f"Input text: {len(content):,} characters")
                        break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                raise ValueError(f"Unable to read file: {file_path}")

            # Process with OpenAI
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            sanitized_base_name = sanitize_filename(base_name)
            content_with_filename = f"《{sanitized_base_name}》\n{content}"
            extract_result = process_text_with_openai(client, GPT_MODEL_EXTRACT, EXTRACT_PROMPT, content_with_filename)
            
            if not extract_result:
                raise ValueError("Empty response from OpenAI API")

            # Save result to extract target folder
            os.makedirs(EXTRACT_FOLDER, exist_ok=True)
            extract_target_path = get_next_available_filename(EXTRACT_FOLDER, base_name, '_e')
            with open(extract_target_path, 'w', encoding='utf-8') as f:
                f.write(extract_result)
            logging.info(f"Output text: {len(extract_result):,} characters")

            # Merge extract content to markdown file in Obsidian sync folder
            md_base_name = sanitize_filename(base_name)  # no _p or _e, sanitized
            os.makedirs(OBSIDIAN_SYNC_FOLDER, exist_ok=True)
            md_path, found_date = find_most_recent_md_by_prefix(OBSIDIAN_SYNC_FOLDER, md_base_name)
            datecode = datetime.now().strftime('%y%m%d')
            if md_path is None:
                # No matching file, create new with today
                md_filename = f"{md_base_name}_{datecode}.md"
                md_path = os.path.join(OBSIDIAN_SYNC_FOLDER, md_filename)
                # Write pretext content to .md file
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                # Add link to Whisper.md as the first new line
                whisper_md_path = os.path.join(OBSIDIAN_SYNC_FOLDER, 'Whisper.md')
                link_code = f"[[{md_base_name}_{datecode}]]\n"
                try:
                    if os.path.exists(whisper_md_path):
                        with open(whisper_md_path, 'r', encoding='utf-8') as f:
                            lines = f.readlines()
                        if lines:
                            lines.insert(1, link_code)
                        else:
                            lines = [link_code]
                        with open(whisper_md_path, 'w', encoding='utf-8') as f:
                            f.writelines(lines)
                    else:
                        with open(whisper_md_path, 'w', encoding='utf-8') as f:
                            f.write(link_code)
                except Exception as e:
                    logging.error(f"Error updating Whisper.md: {str(e)}")
            if os.path.exists(md_path):
                with open(md_path, 'r', encoding='utf-8') as f:
                    pretext_md_content = f.read()
            else:
                pretext_md_content = ''
            merged_content = f"\n{extract_result}\n\n---\n\n{pretext_md_content}"
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(merged_content)

            logging.info(f"Processed and saved to: {extract_target_path}")

            # Move the _p.txt file to PRETEXT_FOLDER for archiving
            os.makedirs(PRETEXT_FOLDER, exist_ok=True)
            pretext_target_path = os.path.join(PRETEXT_FOLDER, os.path.basename(file_path))
            shutil.move(file_path, pretext_target_path)
            logging.info(f"Archived _p.txt to: {pretext_target_path}")

        except Exception as e:
            logging.error(f"Error processing file: {str(e)}")
            if 'extract_result' in locals():
                with open(extract_target_path + ".error.txt", 'w', encoding='utf-8') as f:
                    f.write(f"Error: {str(e)}\nPartial response:\n{extract_result}")

# Process files in the pretext queue using OpenAI
def process_pretext_queue():
    while True:
        try:
            file_path = pretext_queue.get()
            with processing_lock:
                PretextHandler().process_pretext(file_path)
                # Only mark task as done after successful processing
                pretext_queue.task_done()
        except Exception as e:
            logging.error(f"Pretext queue error: {str(e)}")
            # Mark task as done even if there was an error to prevent queue from blocking
            pretext_queue.task_done()
        time.sleep(1)

# Process files in the extract queue using OpenAI
def process_extract_queue():
    while True:
        try:
            file_path = extract_queue.get()
            with processing_lock:
                ExtractHandler().process_extract(file_path)
                # Only mark task as done after successful processing
                extract_queue.task_done()
        except Exception as e:
            logging.error(f"Extract queue error: {str(e)}")
            # Mark task as done even if there was an error to prevent queue from blocking
            extract_queue.task_done()
        time.sleep(1)

# Scan the watch folder and add existing files to the appropriate queues
def scan_existing_files():
    for filename in os.listdir(WATCH_FOLDER):
        if not filename.lower().endswith('.txt'):
            continue

        file_path = os.path.join(WATCH_FOLDER, filename)
        
        if len(os.path.splitext(filename)[0]) > 60:
            new_name = sanitize_and_trim_filename(filename)
            new_path = os.path.join(WATCH_FOLDER, new_name)
            try:
                if not os.path.exists(new_path):
                    safe_rename(file_path, new_path)
                    file_path = new_path
            except Exception as e:
                logging.error(f"Error renaming file: {str(e)}")
                continue

        if filename.lower().endswith('_p.txt'):
            extract_queue.put(file_path)
        else:
            pretext_queue.put(file_path)

# Main entry point for the text processor and TTML watcher
def main():
    logging.info("Starting text processor")
    
    # Create folders and start threads
    os.makedirs(ORIGINAL_FOLDER, exist_ok=True)
    
    threading.Thread(target=process_pretext_queue, daemon=True).start()
    threading.Thread(target=process_extract_queue, daemon=True).start()
    
    # Process existing files
    scan_existing_files()
    
    # Start file watchers
    observer = Observer()
    observer.schedule(PretextHandler(), WATCH_FOLDER, recursive=False)
    observer.schedule(ExtractHandler(), WATCH_FOLDER, recursive=False)
    observer.start()
    
    try:
        while True:
            # --- TTML watcher logic ---
            # Only process TTML files when no OpenAI task is running (lock is free)
            ttml_files = [fn for fn in os.listdir(WATCH_FOLDER) if fn.lower().endswith('.ttml')]
            for fn in ttml_files:
                src = os.path.join(WATCH_FOLDER, fn)
                if os.path.exists(src + '.processing'):
                    continue
                if not is_file_ready(src):
                    continue
                # Try to acquire the lock (block until available)
                logging.info(f"Waiting for lock to process TTML: {src}")
                with processing_lock:
                    logging.info(f"TTML conversion started: {src}")
                    handle_ttml(src)
                    logging.info(f"TTML conversion finished: {src}")
            time.sleep(2)
    except KeyboardInterrupt:
        observer.stop()
        observer.join()
        logging.info("Program stopped")


# Process text with the OpenAI API using a system prompt
def process_text_with_openai(client, model, system_prompt, text):
    logging.info(f"Processing text: {len(text):,} characters")
    
    response = call_openai_with_retry(client, model, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text}
    ])
    
    logging.info(f"Response text: {len(response):,} characters")
    return response

# Find the most recent markdown file by prefix in a folder
def find_most_recent_md_by_prefix(folder, prefix):
    pattern = re.compile(rf'^{re.escape(prefix)}_(\d{{6}})\.md$', re.IGNORECASE)
    most_recent = None
    most_recent_date = None
    for fname in os.listdir(folder):
        match = pattern.match(fname)
        if match:
            datecode = match.group(1)
            if most_recent_date is None or datecode > most_recent_date:
                most_recent = fname
                most_recent_date = datecode
    if most_recent:
        return os.path.join(folder, most_recent), most_recent_date
    return None, None

# Safely rename a file if the target does not exist
def safe_rename(old_path, new_path):
    try:
        if not os.path.exists(new_path):
            os.rename(old_path, new_path)
            return new_path
        return old_path
    except Exception as e:
        logging.error(f"Rename failed {old_path} -> {new_path}: {e}")
        return old_path

# --- Intelligent Chunk Merging ---
def intelligent_merge_chunks(chunks, window=40, min_len=4):
    """
    Merge a list of text chunks, eliminating redundant overlapping content between adjacent chunks.
    For each pair, find the longest common substring between the end of the previous chunk and the start of the next chunk (within a window).
    """
    if not chunks:
        return ''
    if len(chunks) == 1:
        return chunks[0]

    def longest_common_substring(a, b):
        # Returns (start_a, start_b, length) of the longest common substring
        max_len = 0
        start_a = start_b = 0
        dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
        for i in range(1, len(a) + 1):
            for j in range(1, len(b) + 1):
                if a[i - 1] == b[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                    if dp[i][j] > max_len:
                        max_len = dp[i][j]
                        start_a = i - max_len
                        start_b = j - max_len
        return start_a, start_b, max_len

    merged = chunks[0]
    for i in range(1, len(chunks)):
        prev = merged[-window:] if len(merged) > window else merged
        curr = chunks[i][:window] if len(chunks[i]) > window else chunks[i]
        start_a, start_b, lcs_len = longest_common_substring(prev, curr)
        if lcs_len >= min_len:
            # Find the position in the full merged and current chunk
            merged_pos = len(merged) - len(prev) + start_a
            curr_pos = start_b + lcs_len
            merged = merged[:merged_pos] + prev[start_a:start_a + lcs_len] + chunks[i][curr_pos:]
        else:
            merged += chunks[i]
    return merged

if __name__ == "__main__":
    main() 
