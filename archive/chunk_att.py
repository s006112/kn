"""
Responsibility:
Routes email attachments to type-specific extractors (PDF/Word/Excel), normalizes their text into fixed-size chunks, and wraps outputs as `chunk_json.Task` units for downstream chunk writing.

Used by:
* rag/email_01_mbox_to_chunks.py

Pipelines:
- iter_attachments -> detect_type -> extract_text -> chunk_fixed -> build_tasks

Invariants:
- Attachments with missing filenames or empty payloads are skipped.
- `seq` is 1-based within each attachment's chunk list.
- Returned tasks are `chunk_json.Task` objects with `text` and `metadata`.

Out of scope:
- File-type-specific parsing and OCR (handled by `chunk_pdf`, `chunk_doc`, `chunk_xls`).
- JSONL persistence (handled by `chunk_json.JsonlWriter`).
"""

import logging
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple

from helper.helper_parsing_doc import WORD_EXTS, extract_text_from_doc, extract_text_from_docx
from chunk_json import Task
from helper.helper_parse_pdf_to_raw import get_pdf_full_text
from helper.helper_parsing_xls import XLS_EXTS, extract_excel_text

PDF_EXTS = {".pdf"}

def extract_pdf_attachment_tasks(
    data: bytes,
    filename: str,
    base_meta: dict,
    max_len: int,
) -> List[Tuple[str, dict]]:
    """
    Purpose:
    Produce fixed-size `(chunk_text, metadata)` tuples for a PDF payload.

    Inputs:
    - data: PDF bytes.
    - filename: Filename used for metadata fields.
    - base_meta: Base metadata to merge into each chunk metadata dict.
    - max_len: Fixed chunk size in characters.

    Outputs:
    - List of `(chunk_text, metadata)` tuples with 1-based `seq`.

    Side effects:
    - Calls `get_pdf_full_text` (which may run OCR fallback and emit logs).

    Failure modes:
    - Returns `[]` when no non-empty chunks are produced.
    """

    full_text = get_pdf_full_text(data, filename)
    return build_attachment_tasks(
        full_text,
        base_meta=base_meta,
        file_type="pdf",
        filename=filename,
        max_len=max_len,
    )

def extract_word_attachment_tasks(
    data: bytes,
    filename: str,
    content_type: str,  # 保持签名不变
    base_meta: dict,
    max_len: int,
) -> List[Tuple[str, dict]]:
    """
    Purpose:
    Produce fixed-size `(chunk_text, metadata)` tuples for a single Word attachment.

    Inputs:
    - data: Attachment bytes.
    - filename: Attachment filename (used for extension detection and logging).
    - content_type: Attachment content type (currently unused; retained for compatibility).
    - base_meta: Base metadata applied to all generated chunks.
    - max_len: Maximum chunk length in characters.

    Outputs:
    - List of `(chunk_text, metadata)` tuples; returns `[]` when unsupported or empty.

    Side effects:
    - Emits log messages describing extraction outcomes.

    Failure modes:
    - Returns `[]` when extraction fails or yields no text.
    """

    suffix = Path(filename).suffix.lower()
    if suffix not in WORD_EXTS:
        return []

    # choose extractor
    if suffix == ".docx":
        paragraphs = extract_text_from_docx(data)
    else:  # .doc legacy
        paragraphs = extract_text_from_doc(data)

    if not paragraphs:
        return []

    # 1) 合并所有段落为一段（维持现有策略）
    full_text = join_nonempty_segments(text for _, text in sorted(paragraphs.items()))

    file_type = "docx" if suffix == ".docx" else "doc"
    tasks = build_attachment_tasks(
        full_text,
        base_meta=base_meta,
        file_type=file_type,
        filename=filename,
        max_len=max_len,
    )

    if tasks:
        logging.info("Extracted attachment %s (%d chunks)", filename, len(tasks))
    return tasks


def join_nonempty_segments(segments: Iterable[str], *, separator: str = "\n\n") -> str:
    """
    Purpose:
    Join segments with a separator while dropping blank/whitespace-only segments.

    Inputs:
    - segments: Iterable of text segments.
    - separator: Separator inserted between non-empty segments.

    Outputs:
    - A single string containing the filtered segments joined by `separator`.

    Side effects:
    - None.

    Failure modes:
    - None.
    """

    filtered = [segment for segment in segments if segment and segment.strip()]
    return separator.join(filtered)


def iter_fixed_chunks(text: str, max_len: int) -> Iterator[str]:
    """
    Purpose:
    Yield fixed-width slices of `text`, skipping whitespace-only chunks.

    Inputs:
    - text: Source text to slice.
    - max_len: Maximum chunk length in characters.

    Outputs:
    - Iterator of chunk strings.

    Side effects:
    - None.

    Failure modes:
    - Returns an empty iterator when `max_len <= 0` or `text` is falsy.
    """

    if max_len <= 0 or not text:
        return (chunk for chunk in ())
    return (
        chunk
        for chunk in (
            text[start : start + max_len] for start in range(0, len(text), max_len)
        )
        if chunk.strip()
    )


def build_attachment_metadata(base_meta: dict, *, file_type: str, filename: str, seq: int) -> dict:
    """
    Purpose:
    Build metadata for a single attachment chunk by extending a base metadata dict.

    Inputs:
    - base_meta: Base metadata to copy/extend.
    - file_type: Attachment type label (e.g. `"pdf"`, `"docx"`, `"excel"`).
    - filename: Original attachment filename.
    - seq: 1-based chunk sequence number within the attachment.

    Outputs:
    - A metadata dict containing the base fields plus attachment-specific keys.

    Side effects:
    - None.

    Failure modes:
    - None.
    """

    return {
        **base_meta,
        "part": "attachment",
        "file_type": file_type,
        "attachment": filename,
        "seq": seq,
    }


def build_attachment_tasks(
    full_text: str,
    *,
    base_meta: dict,
    file_type: str,
    filename: str,
    max_len: int,
) -> List[Tuple[str, dict]]:
    """
    Purpose:
    Split `full_text` into fixed-size chunks and attach per-chunk metadata.

    Inputs:
    - full_text: Text extracted from a single attachment.
    - base_meta: Base metadata to extend for all chunks from this attachment.
    - file_type: Attachment type label.
    - filename: Original attachment filename.
    - max_len: Maximum chunk length in characters.

    Outputs:
    - List of `(chunk_text, metadata)` tuples with 1-based `seq`.

    Side effects:
    - None.

    Failure modes:
    - Returns an empty list when no non-empty chunks are produced.
    """

    chunks = list(iter_fixed_chunks(full_text, max_len))
    return [
        (chunk, build_attachment_metadata(base_meta, file_type=file_type, filename=filename, seq=i + 1))
        for i, chunk in enumerate(chunks)
    ]


def extract_attachment_tasks(
    email,
    base_meta: dict,
    max_text_len: int,
) -> List[Task]:
    """
    Purpose:
    Iterate email attachments, extract their text via type-specific handlers, and return a flat list of chunking tasks.

    Inputs:
    - email: Email/message object exposing `iter_attachments()`.
    - base_meta: Base metadata applied to every produced task.
    - max_text_len: Maximum chunk length passed to attachment handlers.

    Outputs:
    - List of `Task` objects, each containing chunk text and metadata.

    Side effects:
    - Logs warnings/errors for missing filenames, empty payloads, unsupported extensions, and extraction failures.
    - Imports attachment handler modules at call time.

    Failure modes:
    - Exceptions raised by individual attachment handlers are caught; failing attachments yield no tasks.
    """

    tasks: List[Task] = []
    for part in email.iter_attachments():
        items: List[Tuple[str, dict]] = []
        try:
            fn = part.get_filename()
            if not fn:
                logging.warning(
                    "Skipping attachment with missing filename (message %s)",
                    base_meta.get("email_id", ""),
                )
                continue

            ext = Path(fn).suffix.lower()
            data = part.get_payload(decode=True)
            if not data:
                logging.warning("Skipping attachment with no payload: %s", fn)
                continue

            ctype = part.get_content_type()

            if ext in PDF_EXTS:
                items = extract_pdf_attachment_tasks(data, fn, base_meta, max_text_len)
            elif ext in WORD_EXTS:
                items = extract_word_attachment_tasks(data, fn, ctype, base_meta, max_text_len)
            elif ext in XLS_EXTS:
                text = extract_excel_text(data, fn)
                if text:
                    items = build_attachment_tasks(
                        text,
                        base_meta=base_meta,
                        file_type="excel",
                        filename=fn,
                        max_len=max_text_len,
                    )
            else:
                logging.debug("Unsupported attachment type: %s", fn)
        except Exception as e:
            logging.error(
                "Error processing attachment in message %s: %s",
                base_meta.get("email_id", ""),
                e,
                exc_info=True,
            )
        tasks.extend(Task(text, meta) for text, meta in items)
    return tasks
