#!/usr/bin/env python3

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
    """Split text using a regex separator while optionally preserving it."""
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
    """Local drop-in replacement for langchain's recursive splitter."""

    def __init__(
        self,
        *,
        chunk_size: int = 4000,
        chunk_overlap: int = 200,
        separators: List[str] | None = None,
        keep_separator: bool | str = True,
        strip_whitespace: bool = True,
    ) -> None:
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
        return self._split_text(text, self.separators)

    def _split_text(self, text: str, separators: List[str]) -> List[str]:
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
        if not docs:
            return None
        text = separator.join(docs)
        return self._clean_text(text)

    def _clean_text(self, text: str) -> str:
        return text.strip() if self.strip_whitespace else text


def reconstruct_fragmented_sentences(chunks: List[str]) -> List[str]:
    """修复跨 chunk 被错误拆分的句子"""
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
    """預處理完整郵件內容，僅保留 email 專屬清洗（其餘交由 sanitize_text）"""
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
    """切割長文本為合適大小的 chunk"""
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
    """文本处理的基本任务单位"""
    text: str
    metadata: dict


def _chunk_single_task(args):
    """
    顶层函数用于多进程：接收一个 tuple 拆包，返回 list of (chunk_text, metadata with seq)
    args: (text, metadata, splitter, min_chunk_size)
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
    """并行切分文本任务，优先用多进程绕开 GIL；可回退到线程池（通过环境变量控制）。"""

    def __init__(self, cfg, tracker) -> None:
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
    """Write processed chunks to a single JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None
        self.chunk_count = 0

    def __enter__(self) -> "JsonlWriter":
        self.handle = self.path.open("w", encoding="utf-8")
        return self

    def write_chunks(self, chunks: Iterable[Tuple[str, dict]]) -> int:
        """Write chunks to file, return number written."""
        if self.handle is None:
            raise RuntimeError("JsonlWriter must be opened before writing")

        count = 0
        for chunk, meta in chunks:
            record = {
                "metadata": {
                    **meta,
                    "seq": meta.get("seq", 1),  # fallback to 1 if not present
                    "chunk_length": len(chunk),
                    "word_count": len(re.findall(r"\b\w+\b", chunk)),
                },
                "content": chunk,
            }
            try:
                self.handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.chunk_count += 1
                count += 1
            except Exception as e:
                logger.error("❌ Failed to write chunk: %s", e, exc_info=True)
        return count

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.handle:
            self.handle.close()
