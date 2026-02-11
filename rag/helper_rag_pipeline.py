#!/usr/bin/env python3
"""
helper_rag_pipeline.py
Responsibility:
Implements a self-contained RAG engine (`RagEngine`) for the local `"standard"` and `"mbox"` indexes: loads chunk text/metadata from SQLite, reconstructs an in-memory embedding matrix from a FAISS index, embeds queries, performs brute-force top-k similarity search, and calls an LLM with retrieved context.

Used by:
* ali_llm.py
* gui_web_rag.py

Pipelines:
- load_chunks -> load_vectors -> normalize_vectors -> load_system_prompt -> embed_query -> knn_search -> build_prompt -> call_llm

Invariants:
- Embedding matrix `E` is L2-normalized once after loading.
- Query embeddings returned by `helper.helper_embedding.embed` are L2-normalized.
- `answer_question` returns `("", "")` for empty/whitespace-only questions.
- Raises `ValueError` when the number of loaded texts does not match the FAISS vector count.

Out of scope:
- Building the FAISS index and populating the SQLite metadata store.
- Chunking/sanitization of source documents.
- Any retrieval semantics beyond brute-force top-k.

Planned semantic extensions (Email RAG compatibility roadmap):

Step 1 (DONE):
- Brute-force KNN retrieval on embedding matrix.
  - Acts as the baseline retrieval engine.

Step 4:
- Add score threshold filtering.
  - Discard low-confidence hits based on similarity score.
  - Fallback behavior: keep the best hit when all scores fall below threshold.

Step 5:
- Add email-level expansion policy.
  - After selecting top emails, expand each email into multiple chunks:
    - Order by seq / page
    - Limit by CHUNKS_PER_EMAIL
    - Stop when token budget is exceeded.

Step 6:
- Add token budget control.
  - Approximate token usage from chunk length.
  - Guarantee LLM prompt stays under MAX_TOKENS.

Step 7:
- Restore Email UI semantics.
  - subject / date display
  - per-email similarity ranking table
  - expanded chunk count per email

Design principle:
This engine intentionally separates:
- Core retrieval infrastructure (vectors, DB, embeddings)
from
- Domain-specific semantic policies (Email grouping, MMR, thresholds, expansion).

This allows:
- One unified RAG engine
- Multiple semantic policies layered on top
- Safe, incremental migration from legacy Email RAG to standard architecture
without rebuilding storage or embeddings.

"""
from __future__ import annotations

import sqlite3
import json
import numpy as np
import faiss
from pathlib import Path
from typing import Any, Dict, List, Tuple

from helper.utils_llm import call_llm
from rag.helper_faiss_embedding import embed


LLM_MODEL = "sonar-pro"   # sonar, gpt-5.1
SEARCH_BACKEND = "faiss"   # "faiss" | "brute"
TOP_K = 10
CANDIDATE_K = 80
SCORE_THRESHOLD = 0.4
ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT_PATH = ROOT / "prompt/prompt_rag_system.txt"
faiss_dir = ROOT / "data/faiss"

def get_faiss_artifact_paths(mode: str) -> tuple[Path, Path]:
    """
    Purpose:
    Return `(sqlite_path, index_path)` for the given RAG mode.

    Inputs:
    - mode: Either `"standard"` or `"mbox"`.

    Outputs:
    - `(sqlite_path, index_path)` matching:
      - `data/faiss/{mode}_metadata.sqlite`
      - `data/faiss/{mode}_faiss.index`

    Side effects:
    - None.

    Failure modes:
    - Raises `ValueError` when `mode` is not one of `"standard"` or `"mbox"`.
    """
    if mode not in {"standard", "mbox"}:
        raise ValueError(f"Unknown RAG mode: {mode!r} (expected 'standard' or 'mbox')")
    
    return (
        faiss_dir / f"{mode}_metadata.sqlite",
        faiss_dir / f"{mode}_faiss.index",
    )


def get_rag_engine(mode: str = "standard"):
    """
    Purpose:
    Return a newly constructed `RagEngine` configured for `mode`.

    Inputs:
    - mode: Either `"standard"` or `"mbox"`.

    Outputs:
    - A `RagEngine` instance.

    Side effects:
    - Loads SQLite chunks, FAISS vectors, and the system prompt during initialization.

    Failure modes:
    - Propagates exceptions from `RagEngine` initialization (missing files, DB errors, etc.).
    """
    return RagEngine(mode=mode)


def _load_all_chunks(db_path: Path) -> Tuple[List[str], List[Dict[str, Any]]]: 
    """
    Purpose:
    Load chunk text and parsed metadata JSON from a SQLite database.

    Inputs:
    - db_path: Path to the SQLite file containing a `chunks` table.

    Outputs:
    - `(texts, metas)` where `texts` is a list of `chunk_text` and `metas` is a list of metadata dicts aligned by row order.

    Side effects:
    - Opens and closes a SQLite connection.

    Failure modes:
    - Propagates `sqlite3` errors for missing tables or unreadable databases.
    - Propagates JSON parsing errors for invalid `metadata_json` strings.
    """

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT chunk_text, metadata_json FROM chunks ORDER BY vector_id"
        ).fetchall()
    finally:
        conn.close()

    texts, metas = [], []
    for text, meta_json in rows:
        texts.append(text)
        metas.append(json.loads(meta_json) if meta_json else {})
    return texts, metas


def _load_embedding_matrix(index_path: Path) -> np.ndarray: 
    """
    Purpose:
    Load a FAISS index from disk and reconstruct all stored vectors as a matrix.

    Inputs:
    - index_path: Path to a FAISS index file.

    Outputs:
    - NumPy array of shape `(ntotal, dim)` and dtype `float32`.

    Side effects:
    - Reads from the filesystem and loads a FAISS index.

    Failure modes:
    - Raises `FileNotFoundError` when the index path does not exist.
    - Raises `ValueError` when the loaded FAISS index does not support vector reconstruction.
    """

    if not index_path.exists():
        raise FileNotFoundError(f"FAISS index not found: {index_path}")
    index = faiss.read_index(str(index_path))
    if index.ntotal == 0:
        return np.zeros((0, index.d), dtype=np.float32)
    try:
        vectors = index.reconstruct_n(0, index.ntotal)
    except RuntimeError:
        raise ValueError("Loaded FAISS index does not support vector reconstruction")
    return np.asarray(vectors, dtype=np.float32)


def brute_force_knn(E: np.ndarray, q: np.ndarray, k: int): 
    """
    Purpose:
    Compute top-k neighbors by brute-force dot product between an embedding matrix and a query vector.

    Inputs:
    - E: Embedding matrix shaped `(n, d)`.
    - q: Query vector shaped `(d,)`.
    - k: Number of neighbors to return.

    Outputs:
    - `(idx, scores)` where `idx` is a 1D array of selected row indices and `scores` are the corresponding dot products.
      The number of returned neighbors is `min(k, E.shape[0])`.

    Side effects:
    - None.

    Failure modes:
    - Propagates NumPy shape errors when `E` and `q` are incompatible.
    """

    scores = E @ q
    idx = np.argsort(scores)[-k:][::-1]
    return idx, scores[idx]

def knn_search_switch(
    *,
    backend: str,
    E: np.ndarray,
    index,
    metas: List[Dict[str, Any]],
    q: np.ndarray,
    k: int,
):
    """
    Minimal switch helper.

    backend:
        "brute"  -> numpy brute force
        "faiss"  -> IndexFlat.search()

    Returns:
        idx, scores   (same shape/semantics as brute_force_knn)
    """
    def _dedup_by_score_and_word(idx_arr, score_arr):
        seen = set()
        kept_idx = []
        kept_scores = []
        for i, s in zip(idx_arr, score_arr):
            meta = metas[int(i)] or {}
            word_count = int(meta.get("word", 0) or 0)
            key = (float(s), word_count)
            if key in seen:
                continue
            seen.add(key)
            kept_idx.append(int(i))
            kept_scores.append(float(s))
        return np.asarray(kept_idx, dtype=int), np.asarray(kept_scores, dtype=float)

    if backend == "brute":
        idx, scores = brute_force_knn(E, q, k)
        return _dedup_by_score_and_word(idx, scores)

    if backend == "faiss":
        D, I = index.search(q[None, :], k)
        return _dedup_by_score_and_word(I[0], D[0])

    raise ValueError(f"Unknown backend: {backend}")


def _build_similarity_table(
    top_idx,
    top_scores,
    metas,
    *,
    page_key: str,
):
    table = [
        "| score | doc | date | doc_type | page | word |",
        "|---:|---|---|---|---:|---:|",
    ]
    total_words = 0
    for i, s in zip(top_idx, top_scores):
        meta = metas[i] or {}
        doc = meta.get("subject") or meta.get("doc_id")
        doc_date = meta.get("date")
        doc_type = meta.get("doc_type")
        page = meta.get(page_key)
        word_count = meta.get("word", 0) or 0
        total_words += int(word_count)
        table.append(
            f"| {float(s):.4f} | {doc} | {doc_date} | {doc_type} | {page} | {word_count} |"
        )
    table.append(f"Total word count: {total_words}")
    return "\n".join(table)


class RagEngine:
    """
    Responsibility:
    Encapsulates RAG initialization (loading chunks/index/model) and query-time retrieval + LLM answering.
    """

    def __init__(self, *, mode: str = "mbox"):
        """
        Purpose:
        Load chunks from SQLite, load vectors from FAISS, normalize vectors, and load the system prompt.

        Inputs:
        - mode: Either `"standard"` or `"mbox"`.

        Outputs:
        - None.

        Side effects:
        - Loads FAISS index vectors into memory.
        - Initializes an embedding wrapper; the underlying embedding model is loaded lazily on first embedding call.
        - Reads a prompt file from disk.
        - Prints an initialization message.

        Failure modes:
        - Raises on DB/index read failures.
        - Raises `ValueError` when the loaded chunk count does not match vector count.
        - Propagates filesystem errors when reading the system prompt fails.
        """
        db_path, index_path = get_faiss_artifact_paths(mode)

        print("Initializing RagEngine: Loading FAISS index and Embedding Model...")

        self.texts, self.metas = _load_all_chunks(db_path)

        # keep brute compatibility
        self.E = _load_embedding_matrix(index_path)

        # faiss search path
        self.index = faiss.read_index(str(index_path))

        if len(self.texts) != self.E.shape[0]:
            raise ValueError("Mismatch between metadata rows and embedding matrix size")

        # normalize only brute matrix (runtime safe)
        norms = np.linalg.norm(self.E, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.E = self.E / norms

        self.SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


    def answer_question(self, question: str) -> tuple[str, str]:
        """
        Purpose:
        Answer a question by retrieving top-k chunks and calling the configured LLM with a context+question prompt.

        Inputs:
        - question: User question string.

        Outputs:
        - `(answer_text, table_str)` where `table_str` is a Markdown similarity table for debugging/logging.

        Side effects:
        - Runs embedding model inference and calls `call_llm`.

        Failure modes:
        - Returns `("", "")` when `question` is empty after stripping.
        - Propagates exceptions from embedding, retrieval, and LLM calls.
        """
        q = question.strip()
        if not q:
            return "", ""

        q_vec = embed([q])[0]
        cand_idx, cand_scores = knn_search_switch(
            backend=SEARCH_BACKEND,
            E=self.E,
            index=self.index,
            metas=self.metas,
            q=q_vec,
            k=CANDIDATE_K,
        )

        # brute_force_knn score 由高到低排序
        top_idx = np.asarray(cand_idx[:TOP_K], dtype=int)
        top_scores = np.asarray(cand_scores[:TOP_K], dtype=float)

        keep_mask = top_scores >= SCORE_THRESHOLD
        if not np.any(keep_mask):
            # 保底：至少保留最相似的一个
            keep_mask[np.argmax(top_scores)] = True
        top_idx = np.asarray(top_idx)[keep_mask]
        top_scores = top_scores[keep_mask]

        # 直接使用原始文本作為 snippet，前綴已在 `rag/std_03_txt_to_chunks.py` 中注入
        snippets = [self.texts[i] for i in top_idx]
        context = "\n\n".join(snippets)

        prompt = f"{context}\n\nQuestion: {q}"
        table_str = _build_similarity_table(
            top_idx,
            top_scores,
            self.metas,
            page_key="page",
        )

        top_idx_set = set(int(i) for i in top_idx)
        score_by_idx = {int(i): float(s) for i, s in zip(cand_idx, cand_scores)}
        failed_idx = [int(i) for i in cand_idx if int(i) not in top_idx_set]
        if failed_idx:
            failed_scores = [score_by_idx[i] for i in failed_idx]
            failed_table = _build_similarity_table(
                failed_idx,
                failed_scores,
                self.metas,
                page_key="page",
            )
            table_str = f"{table_str}\n\nFailed CANDIDATE_K (not selected):\n\n{failed_table}"
        else:
            table_str = f"{table_str}\n\nFailed CANDIDATE_K (not selected): (none)"
        
        result_text = call_llm(
            LLM_MODEL,
            system_prompt=self.SYSTEM_PROMPT,
            user_text=prompt,
            max_retries=2,
        )

        return result_text.strip(), table_str
