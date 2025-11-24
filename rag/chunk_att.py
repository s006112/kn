import logging
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple

from chunk_json import Task


def join_nonempty_segments(segments: Iterable[str], *, separator: str = "\n\n") -> str:
    """Join segments with separator, skipping blanks while preserving original content."""
    filtered = [segment for segment in segments if segment and segment.strip()]
    return separator.join(filtered)


def iter_fixed_chunks(text: str, max_len: int) -> Iterator[str]:
    """Yield non-empty fixed-width slices from text."""
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
    """Compose metadata for an attachment chunk."""
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
    """Split full_text into fixed chunks and attach metadata for downstream processing."""
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
    from chunk_doc import WORD_EXTS, extract_word_attachment_tasks
    from chunk_pdf import PDF_EXTS, extract_pdf_attachment_tasks
    from chunk_xls import XLS_EXTS, extract_excel_attachment_tasks

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
                items = extract_excel_attachment_tasks(data, fn, ctype, base_meta, max_text_len)
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
