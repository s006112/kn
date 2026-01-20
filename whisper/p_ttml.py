import os
import shutil
import re
import time
import logging
from utils_files import release_text_file_permissions
from xml.dom.minidom import parse

# Recursively extract all text content from XML node and its children
def extract_text(node):
    text = ''
    if node.nodeType == node.TEXT_NODE and node.data.strip():
        text = node.data.strip() + '\n'
    for child in node.childNodes:
        text += extract_text(child)
    return text

# Normalize whitespace - remove all spaces for Chinese text, normalize for others
def process_text(line):
    if re.search(r'[\u4e00-\u9fa5]', line):
        return re.sub(r'\s+', '', line)
    return re.sub(r'\s+', ' ', line.strip())

# Check if file is ready for processing by verifying stable file size
def is_file_ready(path, wait=1.0):
    size1 = os.path.getsize(path)
    time.sleep(wait)
    return size1 == os.path.getsize(path)

# Convert TTML subtitle file to plain text and archive the original
def handle_ttml(path, watch_folder, original_folder, sanitize_and_trim_filename, pretext_suffix: str):
    """Convert a TTML file to plain text and archive the original."""
    lock = path + '.processing'
    filename = os.path.basename(path)

    try:
        # Read file to get character count before processing
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        char_count = len(content)
        logging.info(f"TTML: Start {filename} (characters: {char_count:,})")

        os.rename(path, lock)

        # Get first line to determine file type (XML/TTML vs plain text)
        first = content.split('\n')[0] if content else ''
        content_length = len(content)

        base_name = sanitize_and_trim_filename(os.path.splitext(filename)[0])
        # Destination text file in watch folder
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
        logging.info(f"TTML: Created {output_filename} ({output_length:,} characters)")

        # Use sanitized base_name for consistent archive naming
        archive_filename = base_name + '.ttml'
        archive_path = os.path.join(original_folder, archive_filename)
        shutil.move(lock, archive_path)

        logging.info(f"TTML: Completed {output_filename}")

    except Exception as e:
        logging.error(f"TTML: Error processing {filename}: {e}")
        if os.path.exists(lock):
            try:
                os.rename(lock, path)
            except Exception as restore_error:
                logging.error(f"TTML: Failed to restore file {filename}: {restore_error}")
