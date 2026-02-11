#!/usr/bin/env python3
"""
helper_rag_pipeline.py
Responsibility
This module loads RAG artifacts for a selected mode, executes similarity retrieval over stored embeddings, builds retrieval context, and returns LLM answers with a similarity report table.

Used by:
* rag/ali_llm.py
* gui_web_rag.py

Pipelines:
- resolve_paths -> load_chunks -> load_vectors -> normalize_vectors
- embed_query -> knn_search_switch -> score_threshold -> topk
- context_build -> prompt_assembly -> llm_call

Invariants:
- The metadata row count must equal the reconstructed embedding row count.
- The in-memory embedding matrix (used by brute backend) is L2-normalized before retrieval.
- Empty or whitespace-only questions return `("", "")`.
- Retrieval output order is descending by similarity score before top-k truncation.

Out of scope:
- Building FAISS indexes or writing chunk metadata databases.
- Defining embedding model internals.
- Implementing reranking or retrieval semantics beyond score-threshold filtering and top-k selection.
- Implementing tokenizer-specific policy outside the provided token budget gate.

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


LLM_MODEL = "sonar-pro"   # sonar, sonar-pro, gpt-5.1, gpt-5-mini,
SEARCH_BACKEND = "faiss"   # "faiss" | "brute"
TOP_K = 10
CANDIDATE_K = 200
SCORE_THRESHOLD = 0.4
ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT_PATH = ROOT / "prompt/prompt_rag_system.txt"
faiss_dir = ROOT / "data/faiss"

def get_faiss_artifact_paths(mode: str) -> tuple[Path, Path]:
    """
    Purpose:
    Return FAISS artifact paths for a RAG mode.

    Inputs:
    - mode: Retrieval mode name.

    Outputs:
    - A tuple `(sqlite_path, index_path)` under `data/faiss`.
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
    Create a configured `RagEngine`.

    Inputs:
    - mode: Retrieval mode name.

    Outputs:
    - A new `RagEngine` instance.
    """
    return RagEngine(mode=mode)


def _load_all_chunks(db_path: Path) -> Tuple[List[str], List[Dict[str, Any]]]: 
    """
    Purpose:
    Load chunk text and metadata rows from SQLite.

    Inputs:
    - db_path: Path to a SQLite file containing a `chunks` table.

    Outputs:
    - A tuple `(texts, metas)` aligned by `vector_id` order.
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
    Load a FAISS index and reconstruct its vectors.

    Inputs:
    - index_path: Path to a FAISS index file.

    Outputs:
    - A `float32` array with shape `(ntotal, dim)`.

    Failure modes:
    - Returns an empty (0, dim) matrix when the index contains no vectors.
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
    Compute dot-product similarity rankings.

    Inputs:
    - E: Embedding matrix with shape `(n, d)`.
    - q: Query vector with shape `(d,)`.
    - k: Number of rows to return.

    Outputs:
    - A tuple `(idx, scores)` for descending similarity rows.
    """

    scores = E @ q
    idx = np.argsort(scores)[-k:][::-1]
    return idx, scores[idx]


def dedup_by_score_and_word(
    idx,
    scores,
    metas,
    limit,
):
    """
    Heuristic deduplication based on (score, word_count).

    Notes:
    - Query-dependent
    - Not stable across floating-point perturbations
    - Does NOT express content identity
    """
    seen, out_i, out_s = set(), [], []
    for i, s in zip(idx, scores):
        meta = metas[int(i)] or {}
        key = (float(s), int(meta.get("word", 0) or 0))
        if key in seen:
            continue
        seen.add(key)
        out_i.append(int(i))
        out_s.append(float(s))
        if len(out_i) >= limit:
            break
    return np.asarray(out_i), np.asarray(out_s)


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
    Purpose:
    Run similarity retrieval using the selected backend and apply a
    heuristic deduplication step to produce up to `k` candidates.

    Notes:
    - Deduplication is heuristic and query-dependent.
    - The current deduplication strategy is based on `(score, word_count)`
      and does NOT represent content or thread identity.
    - FAISS backend may over-fetch and iteratively expand the search
      to satisfy `k` results after deduplication.

    Inputs:
    - backend: Search backend name ("faiss" or "brute").
    - E: Normalized embedding matrix (brute backend only).
    - index: FAISS index (faiss backend only).
    - metas: Metadata aligned to vector rows.
    - q: Query embedding vector.
    - k: Requested number of results.

    Outputs:
    - `(idx, scores)` with at most `k` rows, ordered by descending score.
    """
    def brute():
        scores = E @ q
        order = np.argsort(scores)[::-1]
        return order, scores[order]

    def faiss_search(fetch_k):
        D, I = index.search(q[None, :], fetch_k)
        return I[0], D[0]

    total = int(index.ntotal) if backend == "faiss" else E.shape[0]
    if total <= 0:
        return np.empty(0, int), np.empty(0, float)

    fetch_k = min(max(k, 1), total)

    if backend == "brute":
        idx, scores = brute()
        return dedup_by_score_and_word(idx, scores, metas, k)

    if backend == "faiss":
        while True:
            idx, scores = faiss_search(fetch_k)
            idx, scores = dedup_by_score_and_word(idx, scores, metas, k)
            if len(idx) >= k or fetch_k >= total:
                return idx, scores
            fetch_k = min(total, fetch_k * 2)

    raise ValueError(f"Unknown backend: {backend}")


def _build_similarity_table(
    top_idx,
    top_scores,
    metas,
    *,
    page_key: str,
):
    """
    Purpose:
    Build a Markdown table for retrieval scores and selected metadata fields.

    Inputs:
    - top_idx: Iterable of selected metadata indices.
    - top_scores: Iterable of similarity scores aligned to `top_idx`.
    - metas: Metadata list aligned to vector rows.
    - page_key: Metadata key used for the page-like column.

    Outputs:
    - A Markdown table string including a total word-count footer.
    """
    table = [
        "| score | doc | date | file_type | page | word |",
        "|---:|---|---|---|---:|---:|",
    ]
    total_words = 0
    for i, s in zip(top_idx, top_scores):
        meta = metas[i] or {}
        doc = meta.get("subject") or meta.get("doc_id")
        doc_date = meta.get("date")
        file_type = meta.get("file_type")
        page = meta.get(page_key)
        word_count = meta.get("word", 0) or 0
        total_words += int(word_count)
        table.append(
            f"| {float(s):.4f} | {doc} | {doc_date} | {file_type} | {page} | {word_count} |"
        )
    table.append(f"Total word count: {total_words}")
    return "\n".join(table)


def apply_score_threshold(idx, scores, threshold):
    """
    Purpose:
    - Guarantees at least one result when input is non-empty, even if all scores are below threshold.

    Inputs:
    - idx: Candidate indices.
    - scores: Candidate similarity scores.
    - threshold: Minimum score to keep.

    Outputs:
    - A tuple `(idx, scores)` after threshold filtering.
    """
    idx_arr = np.asarray(idx, dtype=int)
    scores_arr = np.asarray(scores, dtype=float)
    if idx_arr.size == 0:
        return idx_arr, scores_arr
    keep_mask = scores_arr >= threshold
    if not np.any(keep_mask):
        keep_mask[np.argmax(scores_arr)] = True
    return idx_arr[keep_mask], scores_arr[keep_mask]


def apply_top_k(idx, scores, k):
    """
    Purpose:
    Truncate ranked rows to top-k.

    Inputs:
    - idx: Ranked indices in descending score order.
    - scores: Ranked scores aligned to `idx`.
    - k: Maximum number of rows to keep.

    Outputs:
    - A tuple `(idx, scores)` limited to `k` rows.
    """
    # This slice preserves the current ranking contract for downstream formatting.
    idx_arr = np.asarray(idx, dtype=int)
    scores_arr = np.asarray(scores, dtype=float)
    limit = max(int(k), 0)
    return idx_arr[:limit], scores_arr[:limit]


def build_context(chunks, metas, tokenizer, max_tokens):
    """
    Purpose:
    Build a context string from retrieved chunks with optional token-budget truncation.

    Inputs:
    - chunks: Ordered chunk strings selected for context.
    - metas: Metadata aligned to `chunks`.
    - tokenizer: Tokenizer object with `encode`, or `None`.
    - max_tokens: Token budget, or `None`.

    Outputs:
    - A single context string joined by blank lines.
    """
    assert len(chunks) == len(metas)

    if tokenizer is None or max_tokens is None:
        return "\n\n".join(chunks)

    budget = int(max_tokens)
    if budget <= 0:
        return ""

    kept = []
    used = 0
    for chunk in chunks:
        chunk_tokens = len(tokenizer.encode(chunk))
        if not kept and chunk_tokens > budget:
            kept.append(chunk)
            break
        if used + chunk_tokens > budget:
            break
        kept.append(chunk)
        used += chunk_tokens

    return "\n\n".join(kept)


class RagEngine:
    """
    Purpose:
    Provide a loaded retrieval engine and query-answer interface.

    Inputs:
    - None.

    Outputs:
    - A class exposing `answer_question`.
    """

    def __init__(self, *, mode: str = "mbox"):
        """
        Purpose:
        Load retrieval artifacts and runtime state.

        Inputs:
        - mode: Retrieval mode name.

        Outputs:
        - None.
        """
        db_path, index_path = get_faiss_artifact_paths(mode)

        print("Initializing RagEngine: Loading FAISS index and Embedding Model...")

        self.texts, self.metas = _load_all_chunks(db_path)

        # The in-memory matrix is retained to keep brute-force backend behavior available.
        self.E = _load_embedding_matrix(index_path)

        # The FAISS index is loaded separately because FAISS search does not use reconstructed matrix state.
        self.index = faiss.read_index(str(index_path))

        if len(self.texts) != self.E.shape[0]:
            raise ValueError("Mismatch between metadata rows and embedding matrix size")

        # Zero norms are clamped to preserve finite values during normalization.
        norms = np.linalg.norm(self.E, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.E = self.E / norms

        self.SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


    def answer_question(self, question: str) -> tuple[str, str]:
        """
        Purpose:
        Retrieve supporting chunks and generate an LLM answer.

        Inputs:
        - question: User question text.

        Outputs:
        - A tuple `(answer_text, table_str)`, where `table_str` includes both selected Top-K
        and non-selected CANDIDATE_K rows for inspection.
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

        filt_idx, filt_scores = apply_score_threshold(cand_idx, cand_scores, SCORE_THRESHOLD)
        top_idx, top_scores = apply_top_k(filt_idx, filt_scores, TOP_K)
        snippets = [self.texts[i] for i in top_idx]
        selected_metas = [self.metas[i] for i in top_idx]
        context = build_context(snippets, selected_metas, tokenizer=None, max_tokens=None)

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
