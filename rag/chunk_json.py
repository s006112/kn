#!/usr/bin/env python3

"""
Responsibility:
Implements email-text chunking utilities (sanitization, preprocessing, recursive splitting, and post-merge heuristics) plus parallel batch processing and JSONL writing for downstream embedding/indexing.

Used by:
* rag/email_01_mbox_to_chunks.py
* rag/chunk_att.py
* archive/std_1_chunk.py
* archive/std_chunker.py

Pipelines:
- sanitize_text -> preprocess_email -> split_recursive -> reconstruct_sentences -> filter_chunks -> batch_process -> write_jsonl

Invariants:
- `Task` carries `text` and `metadata` and is the unit of work for batch chunking.
- Output chunk metadata includes a 1-based `seq` per source task.
- `BatchProcessor` prefers `ProcessPoolExecutor` unless `FORCE_CHUNK_THREAD=1`, and falls back to threads on process-pool failure.
- `JsonlWriter` writes one JSON object per line with top-level metadata fields plus `text`, `char`, and `word`.

Out of scope:
- Attachment extraction (handled by `chunk_att` and type-specific modules).
- Embedding generation and vector index persistence.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor

from helper.helper_sanitize import sanitize_text

logger = logging.getLogger(__name__)

RE_WORDS = re.compile(r"\b[a-zA-Z]{2,}\b")
RE_QUOTE_ONLY = re.compile(r"^>+\s*$")
RE_HEADER = re.compile(r"^(From|To|Subject|Date):")


def _split_text_with_regex(
    text: str,
    separator: str,
    keep_separator: bool | str,
) -> List[str]:
    """
    Purpose:
    Split text by a regex separator, optionally preserving separator matches in the returned segments.

    Inputs:
    - text: Source text to split.
    - separator: Regex pattern used for splitting (already escaped by callers when needed).
    - keep_separator: When falsy, separators are dropped; when truthy, separators are included either at the start or end depending on value.

    Outputs:
    - List of non-empty string segments.

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions from `re.split` on invalid regex patterns.
    """

    if separator:
        if keep_separator:
            splits_ = re.split(f"({separator})", text)
            splits = (
                [splits_[i] + splits_[i + 1] for i in range(0, len(splits_) - 1, 2)]
                if keep_separator == "end"
                else [
                    splits_[i] + splits_[i + 1]
                    for i in range(1, len(splits_), 2)
                    if i + 1 < len(splits_)
                ]
            )
            if len(splits_) % 2 == 0:
                splits += splits_[-1:]
            splits = (
                [*splits, splits_[-1]]
                if keep_separator == "end"
                else [splits_[0], *splits]
            )
        else:
            splits = re.split(separator, text)
    else:
        splits = list(text)
    return [s for s in splits if s]


class RecursiveCharacterTextSplitter:
    """
    Responsibility:
    Split text into overlapping chunks using a recursive set of separators, similar to LangChain's recursive character splitter.

    Invariants:
    - `chunk_size` must be > 0.
    - `chunk_overlap` must be >= 0 and <= `chunk_size`.
    - When `keep_separator` is truthy, separators are preserved in split segments.
    """

    def __init__(
        self,
        *,
        chunk_size: int = 4000,
        chunk_overlap: int = 200,
        separators: List[str] | None = None,
        keep_separator: bool | str = True,
        strip_whitespace: bool = True,
    ) -> None:
        """
        Purpose:
        Configure a recursive splitter with size/overlap constraints and separator preferences.

        Inputs:
        - chunk_size: Target maximum characters per chunk.
        - chunk_overlap: Desired overlap in characters between adjacent chunks.
        - separators: Ordered list of separators to try (from coarse to fine); defaults to `["\\n\\n", "\\n", " ", ""]`.
        - keep_separator: Whether and how to keep separators in the output (`True`, `False`, or `"end"`).
        - strip_whitespace: Whether to strip whitespace from merged chunk text.

        Outputs:
        - None.

        Side effects:
        - None.

        Failure modes:
        - Raises `ValueError` when size/overlap constraints are invalid.
        """

        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be >= 0")
        if chunk_overlap > chunk_size:
            raise ValueError("chunk_overlap cannot exceed chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", " ", ""]
        self.keep_separator = keep_separator
        self.strip_whitespace = strip_whitespace
        self.length_function = len
        self.is_separator_regex = False

    def split_text(self, text: str) -> List[str]:
        """
        Purpose:
        Split a text string into chunks using the configured separator cascade.

        Inputs:
        - text: Source text.

        Outputs:
        - List of chunk strings (may be empty).

        Side effects:
        - None.

        Failure modes:
        - Propagates exceptions from internal splitting logic.
        """

        return self._split_text(text, self.separators)

    def _split_text(self, text: str, separators: List[str]) -> List[str]:
        """
        Purpose:
        Recursively split text using the first separator that matches and merge sub-splits to satisfy chunk size limits.

        Inputs:
        - text: Source text.
        - separators: Remaining separators to try.

        Outputs:
        - List of chunk strings.

        Side effects:
        - None.

        Failure modes:
        - Propagates exceptions from `re.search`/`re.split`.
        """

        final_chunks: List[str] = []
        separator = separators[-1]
        new_separators: List[str] = []

        for index, candidate in enumerate(separators):
            pattern = candidate if self.is_separator_regex else re.escape(candidate)
            if not candidate:
                separator = candidate
                break
            if re.search(pattern, text):
                separator = candidate
                new_separators = separators[index + 1 :]
                break

        pattern = separator if self.is_separator_regex else re.escape(separator)
        splits = _split_text_with_regex(text, pattern, self.keep_separator)

        good_splits: List[str] = []
        merge_separator = "" if self.keep_separator else separator
        for split in splits:
            if self.length_function(split) < self.chunk_size:
                good_splits.append(split)
            else:
                if good_splits:
                    final_chunks.extend(self._merge_splits(good_splits, merge_separator))
                    good_splits = []
                if not new_separators:
                    final_chunks.append(self._clean_text(split))
                else:
                    final_chunks.extend(self._split_text(split, new_separators))

        if good_splits:
            final_chunks.extend(self._merge_splits(good_splits, merge_separator))

        return [chunk for chunk in final_chunks if chunk]

    def _merge_splits(self, splits: Iterable[str], separator: str) -> List[str]:
        """
        Purpose:
        Merge split segments into chunks that respect `chunk_size`, maintaining an overlap window when necessary.

        Inputs:
        - splits: Iterable of split segments.
        - separator: Separator used to join segments when forming chunks.

        Outputs:
        - List of merged chunk strings.

        Side effects:
        - Emits a warning when an intermediate merged chunk exceeds `chunk_size`.

        Failure modes:
        - None.
        """

        separator_len = self.length_function(separator)
        docs: List[str] = []
        current_doc: List[str] = []
        total = 0

        for chunk in splits:
            chunk_len = self.length_function(chunk)
            if (
                total + chunk_len + (separator_len if current_doc else 0)
                > self.chunk_size
            ):
                if total > self.chunk_size:
                    logger.warning(
                        "Created a chunk of size %d, exceeding limit %d",
                        total,
                        self.chunk_size,
                    )
                if current_doc:
                    doc = self._join_docs(current_doc, separator)
                    if doc is not None:
                        docs.append(doc)
                    while total > self.chunk_overlap or (
                        total
                        + chunk_len
                        + (separator_len if current_doc else 0)
                        > self.chunk_size
                        and total > 0
                    ):
                        removal = self.length_function(current_doc[0])
                        total -= removal
                        if len(current_doc) > 1:
                            total -= separator_len
                        current_doc = current_doc[1:]
            current_doc.append(chunk)
            total += chunk_len + (separator_len if len(current_doc) > 1 else 0)

        doc = self._join_docs(current_doc, separator)
        if doc is not None:
            docs.append(doc)
        return docs

    def _join_docs(self, docs: List[str], separator: str) -> str | None:
        """
        Purpose:
        Join a list of segments into a single chunk string and apply final cleaning.

        Inputs:
        - docs: List of segments.
        - separator: Separator to insert between segments.

        Outputs:
        - Joined and cleaned chunk string, or `None` when `docs` is empty.

        Side effects:
        - None.

        Failure modes:
        - None.
        """

        if not docs:
            return None
        text = separator.join(docs)
        return self._clean_text(text)

    def _clean_text(self, text: str) -> str:
        """
        Purpose:
        Optionally strip whitespace from a chunk.

        Inputs:
        - text: Input text.

        Outputs:
        - Cleaned text (stripped if `strip_whitespace` is true).

        Side effects:
        - None.

        Failure modes:
        - None.
        """

        return text.strip() if self.strip_whitespace else text


def reconstruct_fragmented_sentences(chunks: List[str]) -> List[str]:
    """
    Purpose:
    Heuristically merge adjacent chunks when a sentence appears to have been split mid-way.

    Inputs:
    - chunks: List of chunk strings.

    Outputs:
    - List of reconstructed chunk strings.

    Side effects:
    - None.

    Failure modes:
    - None.
    """

    reconstructed: List[str] = []
    current = ""
    for chunk in map(str.strip, chunks or []):
        if not chunk:
            continue
        if (
            current
            and not current.endswith((".", "!", "?", ":", "\n"))
            and not chunk[0].isupper()
            and len(current) < 1200
        ):
            current += " " + chunk
        else:
            if current:
                reconstructed.append(current)
            current = chunk
    if current:
        reconstructed.append(current)
    return reconstructed


def preprocess_email_content(text: str) -> str:
    """
    Purpose:
    Apply email-specific preprocessing after running the shared `sanitize_text` routine.

    Inputs:
    - text: Full email text.

    Outputs:
    - Preprocessed email text with some quote/header/attribution heuristics applied.

    Side effects:
    - None.

    Failure modes:
    - None.
    """

    text = sanitize_text(text)  # 通用清洗統一處理

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    processed = []
    for line in lines:
        if RE_QUOTE_ONLY.match(line):
            continue
        if RE_HEADER.match(line):
            processed.append(line)
        elif "@" in line and ("wrote:" in line or "said:" in line):
            processed.append(f"\n{line}")
        else:
            processed.append(line)

    return "\n".join(processed)


def generate_text_chunks(text: str, splitter, min_size: int) -> List[str]:
    """
    Purpose:
    Generate filtered text chunks from an input string using preprocessing, recursive splitting, reconstruction, and basic quality filters.

    Inputs:
    - text: Source text.
    - splitter: Splitter instance providing `split_text`.
    - min_size: Minimum character length for a chunk to be kept.

    Outputs:
    - List of chunk strings.

    Side effects:
    - May emit debug logs when DEBUG logging is enabled.

    Failure modes:
    - Returns `[]` when the input is too short or all chunks are filtered out.
    """

    if not text or len(text.strip()) < min_size:
        return []

    text = preprocess_email_content(text).strip()
    if len(text) < min_size:
        return []

    raw_chunks = splitter.split_text(text)
    final = reconstruct_fragmented_sentences([
        chunk.strip() for chunk in raw_chunks
        if len(chunk.strip()) >= min_size
    ])

    chunks = [
        c.strip() for c in final
        if len(c) >= min_size and len(RE_WORDS.findall(c)) >= 5
    ]

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Text preprocessing: %d → %d chunks", len(raw_chunks), len(chunks))
    return chunks


@dataclass
class Task:
    """
    Responsibility:
    Basic unit of work for chunking: a text payload plus metadata dict.
    """

    text: str
    metadata: dict


def _chunk_single_task(args):
    """
    Purpose:
    Top-level worker function for process pools: chunk a single `(text, metadata)` input and add per-chunk `seq`.

    Inputs:
    - args: Tuple of `(text, metadata, splitter, min_chunk_size)`.

    Outputs:
    - List of `(chunk_text, metadata)` tuples with 1-based `seq` values.

    Side effects:
    - Emits a warning log when chunking fails.

    Failure modes:
    - Returns `[]` when chunking raises an exception.
    """

    text, meta, splitter, min_chunk_size = args
    try:
        chunks = generate_text_chunks(text, splitter, min_chunk_size)
        out = []
        for i, chunk in enumerate(chunks):
            new_meta = {**meta, "seq": i + 1}
            out.append((chunk, new_meta))
        return out
    except Exception as e:
        # 进程内部捕获，返回空列表
        logger.warning("Chunking inner task failed: %s", e)
        return []


class BatchProcessor:
    """
    Responsibility:
    Chunk a batch of `Task` items in parallel, preferring a process pool and falling back to a thread pool.

    Invariants:
    - `max_workers` is capped at 32.
    - The process pool is skipped when `FORCE_CHUNK_THREAD=1`.
    """

    def __init__(self, cfg, tracker) -> None:
        """
        Purpose:
        Initialize executors and a `RecursiveCharacterTextSplitter` using configuration values.

        Inputs:
        - cfg: Config-like object with `parallel_workers`, `chunk_size`, `chunk_overlap`, and `min_chunk_size`.
        - tracker: Object exposing `update_batch(emails, chunks, duration)`.

        Outputs:
        - None.

        Side effects:
        - Creates a `ThreadPoolExecutor` and stores configuration-derived values.

        Failure modes:
        - Raises if executor initialization fails.
        """

        self.cfg = cfg
        self.tracker = tracker
        # 并行度上限和复用逻辑
        max_workers = min(self.cfg.parallel_workers or 4, 32)
        self.max_workers = max_workers
        # 保留原来的线程池用于 fallback
        self.thread_executor = ThreadPoolExecutor(max_workers=max_workers)
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=cfg.chunk_size,
            chunk_overlap=cfg.chunk_overlap,
            separators=["\n\n", "\n", " ", ""],
        )
        # 环境变量控制是否强制回退线程（便于调试/兼容）
        self.force_thread = os.getenv("FORCE_CHUNK_THREAD", "0") == "1"

    def process(self, tasks: Iterable[Task]) -> List[Tuple[str, dict]]:
        """
        Purpose:
        Chunk a sequence of `Task` items and return flattened `(chunk_text, metadata)` results with per-task `seq`.

        Inputs:
        - tasks: Iterable of `Task` objects.

        Outputs:
        - List of `(chunk_text, metadata)` tuples.

        Side effects:
        - Uses a process pool or thread pool to parallelize chunking.
        - Calls `tracker.update_batch(0, len(results), duration)` with elapsed time.

        Failure modes:
        - Falls back to thread-based processing when process-based chunking fails.
        - Per-task chunking exceptions are logged and yield no output for that task.
        """

        texts = [t.text for t in tasks]
        metas = [t.metadata for t in tasks]
        start = time.time()

        results: List[Tuple[str, dict]] = []

        inputs = [(text, meta, self.splitter, self.cfg.min_chunk_size) for text, meta in zip(texts, metas)]

        use_process = not self.force_thread
        if use_process:
            try:
                with ProcessPoolExecutor(max_workers=self.max_workers) as pool:
                    # 注意：_chunk_single_task 是顶层可 pickle
                    for out in pool.map(_chunk_single_task, inputs):
                        if out:
                            results.extend(out)
            except Exception as e:
                # 退回到线程池（兼容性保底）
                logger.warning("ProcessPoolExecutor failed (%s), falling back to ThreadPoolExecutor", e)
                use_process = False  # 继续到线程版
        if not use_process:
            # 线程版（原逻辑）
            def _chunk_single(text: str, meta: dict) -> List[Tuple[str, dict]]:
                """
                Purpose:
                Thread-based fallback chunking for a single `(text, metadata)` input, adding per-chunk `seq`.

                Inputs:
                - text: Source text.
                - meta: Base metadata dict for the task.

                Outputs:
                - List of `(chunk_text, metadata)` tuples.

                Side effects:
                - None.

                Failure modes:
                - Propagates exceptions from `generate_text_chunks`.
                """

                chunks = generate_text_chunks(text, self.splitter, self.cfg.min_chunk_size)
                return [(chunk, {**meta, "seq": i + 1}) for i, chunk in enumerate(chunks)]

            futures = [self.thread_executor.submit(_chunk_single, text, m) for text, m in zip(texts, metas)]
            for future in as_completed(futures):
                try:
                    results.extend(future.result())
                except Exception as e:
                    logger.warning("Chunking failed: %s", e)

        self.tracker.update_batch(0, len(results), time.time() - start)
        return results


class JsonlWriter:
    """
    Responsibility:
    Context-managed JSONL writer for chunk records, tracking how many chunks have been written.

    Invariants:
    - `write_chunks` requires the writer to be opened via `__enter__`.
    - Each output line is a JSON object with top-level metadata fields plus `text`, `char`, and `word`.
    """

    def __init__(self, path: Path) -> None:
        """
        Purpose:
        Initialize a JSONL writer targeting a specific output path.

        Inputs:
        - path: Output JSONL file path.

        Outputs:
        - None.

        Side effects:
        - None.

        Failure modes:
        - None.
        """

        self.path = path
        self._record_builder = lambda chunk, meta, *, seq, char, word: {
            **meta,
            "seq": seq,
            "char": char,
            "word": word,
            "text": chunk,
        }
        self.handle = None
        self.chunk_count = 0

    def __enter__(self) -> "JsonlWriter":
        """
        Purpose:
        Open the output file handle for writing.

        Inputs:
        - None.

        Outputs:
        - Self, allowing `with JsonlWriter(...) as w: ...`.

        Side effects:
        - Opens a file handle in text mode with UTF-8 encoding.

        Failure modes:
        - Propagates filesystem exceptions when the file cannot be opened.
        """

        self.handle = self.path.open("w", encoding="utf-8")
        return self

    def write_chunks(self, chunks: Iterable[Tuple[str, dict]]) -> int:
        """
        Purpose:
        Write chunk records to the open JSONL file and return the number of successfully written records.

        Inputs:
        - chunks: Iterable of `(chunk_text, metadata)` tuples.

        Outputs:
        - Count of records written in this call.

        Side effects:
        - Writes JSON lines to disk and increments `self.chunk_count`.
        - Emits error logs on write/serialization failures.

        Failure modes:
        - Raises `RuntimeError` if called before `__enter__` opens the file.
        """

        if self.handle is None:
            raise RuntimeError("JsonlWriter must be opened before writing")

        count = 0
        for chunk, meta in chunks:
            seq = meta.get("seq", 1)
            char = len(chunk)
            word = len(re.findall(r"\b\w+\b", chunk))
            record = self._record_builder(chunk, meta, seq=seq, char=char, word=word)
            try:
                self.handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.chunk_count += 1
                count += 1
            except Exception as e:
                logger.error("❌ Failed to write chunk: %s", e, exc_info=True)
        return count

    def __exit__(self, exc_type, exc, tb) -> None:
        """
        Purpose:
        Close the output file handle on context manager exit.

        Inputs:
        - exc_type: Exception type if an exception occurred in the context block.
        - exc: Exception instance if an exception occurred in the context block.
        - tb: Traceback if an exception occurred in the context block.

        Outputs:
        - None.

        Side effects:
        - Closes the file handle when it exists.

        Failure modes:
        - None.
        """

        if self.handle:
            self.handle.close()
