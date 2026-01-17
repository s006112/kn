"""
Helpers for creating and updating Markdown notes and a Whisper index note.

Used by:
* whisper/p_distill.py
* whisper/p_extract.py
* whisper/p_pretext.py

Pipelines:
- folder -> listdir -> regex_match -> max -> md_path
- config -> base_name -> md_path -> write -> permissions
- md_path -> sections -> prepend -> write -> permissions
- whisper_md -> readlines -> insert -> writelines

Invariants:
- `find_most_recent_md_by_prefix` only matches `"{prefix}_YYMMDD.md"` (6 digits).
- `create_or_find_note_for_base_name` always ensures `config["OBSIDIAN_SYNC_FOLDER"]` exists.
- `write_pretext_markdown` always creates a new dated note (`allow_existing=False`).
- `merge_to_markdown` only updates the Whisper index when `md_is_new` is true.
- Index links are inserted as single lines formatted like `[[note_name]]`.

Out of scope:
- Markdown formatting beyond simple section insertion.
- Concurrent edits, file locking, and conflict resolution.
- Obsidian vault sync configuration and link validation.
"""

import os
import logging
import re
from datetime import datetime

from utils_files import release_text_file_permissions


def find_most_recent_md_by_prefix(folder, prefix):
    """
    Purpose:
    - Find the most recent Markdown file in `folder` matching a `{prefix}_YYMMDD.md` pattern.
    Inputs:
    - folder: Directory to search.
    - prefix: Filename prefix to match (case-insensitive).
    Outputs:
    - (md_path, datecode): Full path and the `YYMMDD` date code for the best match, or
      `(None, None)` when no candidates exist.
    Side effects:
    - Reads directory entries via `os.listdir`.
    Failure modes:
    - Propagates `OSError` from `os.listdir`.
    """
    pattern = re.compile(rf'^{re.escape(prefix)}_(\d{{6}})\.md$', re.IGNORECASE)
    candidates = (
        (fname, match.group(1))
        for fname in os.listdir(folder)
        for match in [pattern.match(fname)]
        if match
    )
    best = max(candidates, key=lambda item: item[1], default=None)
    if not best:
        return None, None
    fname, datecode = best
    return os.path.join(folder, fname), datecode


def create_or_find_note_for_base_name(config, base_name: str, *, allow_existing: bool):
    """
    Purpose:
    - Resolve a Markdown note path for `base_name` under `config["OBSIDIAN_SYNC_FOLDER"]`.
    Inputs:
    - config: Mapping containing `OBSIDIAN_SYNC_FOLDER`.
    - base_name: Base note name (prefix before the date code).
    - allow_existing: When true, reuse the most recent existing matching note if present.
    Outputs:
    - (md_path, link_name, md_is_new): Full path, Obsidian link name (stem), and whether the
      note is newly created (path selected for a new datecode) vs reused.
    Side effects:
    - Creates the sync folder if missing (`os.makedirs(..., exist_ok=True)`).
    - May read directory entries when `allow_existing` is true.
    Failure modes:
    - Propagates `OSError` from `os.makedirs` and `os.listdir`.
    """
    folder = config["OBSIDIAN_SYNC_FOLDER"]
    os.makedirs(folder, exist_ok=True)

    if allow_existing:
        md_path, _ = find_most_recent_md_by_prefix(folder, base_name)
        if md_path is not None:
            link_name = os.path.splitext(os.path.basename(md_path))[0]
            return md_path, link_name, False

    datecode = datetime.now().strftime("%y%m%d")
    md_name = f"{base_name}_{datecode}.md"
    md_path = os.path.join(folder, md_name)
    link_name = f"{base_name}_{datecode}"
    return md_path, link_name, True


def write_pretext_markdown(config, base_name: str, content: str) -> str:
    """
    Purpose:
    - Create a new pretext Markdown note and insert its link into `Whisper 000000.md`.
    Inputs:
    - config: Mapping containing `OBSIDIAN_SYNC_FOLDER`.
    - base_name: Base note name used to build `{base_name}_YYMMDD.md`.
    - content: Markdown content to write to the note.
    Outputs:
    - The created note path.
    Side effects:
    - Writes the note file and updates its permissions.
    - Updates `Whisper 000000.md` in the sync folder and updates its permissions.
    Failure modes:
    - Propagates `OSError` from file IO and directory operations.
    """
    md_path, link_name, _ = create_or_find_note_for_base_name(
        config, base_name, allow_existing=False
    )
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(content)
    release_text_file_permissions(md_path)

    whisper_md_path = os.path.join(
        config['OBSIDIAN_SYNC_FOLDER'], 'Whisper 000000.md'
    )
    update_whisper_index_for_pretext(whisper_md_path, link_name)
    return md_path


def merge_to_markdown(md_path, extracts, original_text, labels, whisper_md_path, whisper_link_name, md_is_new):
    """
    Purpose:
    - Prepend extracted sections to a Markdown note and optionally link it from the Whisper index.
    Inputs:
    - md_path: Target Markdown note path to update.
    - extracts: Iterable of extracted text blocks.
    - original_text: Unused input kept for API compatibility.
    - labels: Iterable of section labels aligned with `extracts`.
    - whisper_md_path: Path to the Whisper index Markdown file.
    - whisper_link_name: Link name to insert into the Whisper index.
    - md_is_new: When true, insert the link into the Whisper index; otherwise skip.
    Outputs:
    - None.
    Side effects:
    - Reads/writes `md_path` and updates its permissions.
    - When `md_is_new` is true, may read/write `whisper_md_path` and update its permissions.
    Failure modes:
    - Propagates `OSError` from IO for `md_path`.
    - Logs and suppresses exceptions while updating `whisper_md_path`.
    """
    new_sections = []
    for label, extract in zip(labels, extracts):
        new_sections.append(f"# {label}\n\n{extract}")
    new_content = "\n\n---\n\n".join(new_sections)

    if os.path.exists(md_path):
        with open(md_path, 'r', encoding='utf-8') as f:
            existing_content = f.read()
    else:
        existing_content = ""

    full_content = new_content.strip() + "\n\n\n" + existing_content.strip()

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(full_content)
    release_text_file_permissions(md_path)

    if not md_is_new:
        return

    link_code = f"[[{whisper_link_name}]]\n"

    try:
        if os.path.exists(whisper_md_path):
            with open(whisper_md_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            if any(line.strip() == link_code.strip() for line in lines):
                return
            insert_at = 1
            for i, line in enumerate(lines):
                if line.strip() == "---":
                    insert_at = i + 1
                    break
            lines.insert(insert_at, link_code)
            with open(whisper_md_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        else:
            with open(whisper_md_path, 'w', encoding='utf-8') as f:
                f.write(link_code)
        release_text_file_permissions(whisper_md_path)
    except Exception as e:
        logging.error(f"Error updating Whisper.md: {str(e)}")


def update_whisper_index_for_pretext(whisper_md_path: str, note_name: str) -> None:
    """
    Purpose:
    - Insert a note link line for a pretext note into `Whisper 000000.md`.
    Inputs:
    - whisper_md_path: Path to the Whisper index Markdown file.
    - note_name: Obsidian link name to insert (without surrounding brackets).
    Outputs:
    - None.
    Side effects:
    - Reads/writes `whisper_md_path` and updates its permissions.
    - Logs an error message on failure.
    Failure modes:
    - Logs and suppresses exceptions for all failures during index update.
    """
    link_code = f"[[{note_name}]]\n"
    try:
        if os.path.exists(whisper_md_path):
            with open(whisper_md_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            if lines:
                insert_index = None
                for i, line in enumerate(lines):
                    if line.strip() == "---":
                        insert_index = i + 1
                        break

                if insert_index is not None:
                    if insert_index < len(lines) and lines[insert_index].strip() == "":
                        lines.insert(insert_index + 1, link_code)
                    else:
                        lines.insert(insert_index, "\n")
                        lines.insert(insert_index + 1, link_code)
                else:
                    lines.insert(1, link_code)
            else:
                lines = [link_code]

            with open(whisper_md_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        else:
            with open(whisper_md_path, 'w', encoding='utf-8') as f:
                f.write(link_code)
        release_text_file_permissions(whisper_md_path)
    except Exception as e:
        logging.error(f"Error updating Whisper.md: {str(e)}")
