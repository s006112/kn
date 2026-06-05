#!/usr/bin/env python3
"""
helper_rag_pipeline.py

按指定 mode 載入 RAG 索引與 chunks，執行相似度檢索，組裝上下文，
並回傳 LLM 回答與相似度報表。

使用者：
- ali/ali_llm.py
- gui_web_rag.py

Pipelines:
- resolve_paths -> load_chunks -> load_vectors -> normalize_vectors
- embed_query -> knn_search_switch -> score_threshold -> topk
- context_build -> prompt_assembly -> llm_call

注意：
- metadata 筆數必須等於 embedding 筆數。
- 空問題回傳 `("", "")`。
- 檢索結果依相似度由高到低排序。
"""
from __future__ import annotations

import sqlite3
import json
import numpy as np
import faiss
from pathlib import Path
from typing import Any, Dict, List, Tuple

from helper.helper_llm import call_llm
from rag.faiss_index_builder import build_embedding_text
from rag.helper_faiss_embedding import embed
from rag.helper_query_rewriting import rewrite_query_variants, merge_candidates_maxscore


SEARCH_BACKEND = "faiss"   # "faiss" | "brute"
TOP_K = 10
CANDIDATE_K = 50
SCORE_THRESHOLD = 0.4
ROOT = Path(__file__).resolve().parents[1]
RAG_DIR = Path(__file__).resolve().parent
STANDARD_SYSTEM_PROMPT_PATH = RAG_DIR / "prompt_rag_standard.txt"
GENERAL_SYSTEM_PROMPT_PATH = RAG_DIR / "prompt_rag_general.txt"
faiss_dir = ROOT / "data/faiss"
ENABLE_QUERY_REWRITE = False   # True = current behavior, False = single-query
REWRITE_MAX_VARIANTS = 3

def get_faiss_artifact_paths(mode: str) -> tuple[Path, Path]:
    """
    依 mode 找出對應的 SQLite metadata 與 FAISS index 檔案。
    """
    #if mode not in {"standard", "mbox"}:
    #    raise ValueError(f"Unknown RAG mode: {mode!r} (expected 'standard' or 'mbox')")

    sqlite_paths = sorted(
        p for p in faiss_dir.iterdir()
        if p.is_file() and p.name.startswith(mode) and p.suffix == ".sqlite"
    )
    index_paths = sorted(
        p for p in faiss_dir.iterdir()
        if p.is_file() and p.name.startswith(mode) and p.suffix == ".index"
    )

    if len(sqlite_paths) != 1 or len(index_paths) != 1:
        raise FileNotFoundError(
            f"Expected exactly one .sqlite and one .index artifact "
            f"for RAG mode {mode!r} in {faiss_dir}"
        )

    return sqlite_paths[0], index_paths[0]


def get_rag_engine(mode: str):
    """
    建立指定 mode 的 RAG engine。
    """
    return RagEngine(mode=mode)


def _load_all_chunks(db_path: Path) -> Tuple[List[str], List[Dict[str, Any]]]: 
    """
    從 SQLite 讀取 chunk 文字與 metadata，依 vector_id 對齊。
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
    載入 FAISS index，並重建 embedding matrix。
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
    以 dot product 計算相似度，回傳前 k 筆結果。
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
    用 (score, word_count) 做簡單去重；不是內容層級的去重。
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
    依 backend 執行相似度檢索，並套用簡單去重。
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
    建立檢索結果的 Markdown 相似度表。
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
    依相似度門檻過濾；若全部低於門檻，至少保留最高分一筆。
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
    保留排序後的前 k 筆結果。
    """
    # This slice preserves the current ranking contract for downstream formatting.
    idx_arr = np.asarray(idx, dtype=int)
    scores_arr = np.asarray(scores, dtype=float)
    limit = max(int(k), 0)
    return idx_arr[:limit], scores_arr[:limit]


def build_context(chunks, metas, tokenizer, max_tokens):
    """
    將檢索到的 chunks 組成上下文，可選擇套用 token 上限。
    """
    assert len(chunks) == len(metas)

    context_chunks = [
        build_embedding_text(chunk, meta or {})
        for chunk, meta in zip(chunks, metas)
    ]

    if tokenizer is None or max_tokens is None:
        return "\n\n".join(context_chunks)

    budget = int(max_tokens)
    if budget <= 0:
        return ""

    kept = []
    used = 0
    for chunk in context_chunks:
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
    已載入索引的 RAG 查詢引擎。
    """

    def __init__(self, *, mode: str):
        """
        載入指定 mode 的檢索資料與執行狀態。
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

        # standard 使用專用 prompt；其他 mode 使用通用 prompt。
        system_prompt_path = STANDARD_SYSTEM_PROMPT_PATH if mode == "standard" else GENERAL_SYSTEM_PROMPT_PATH
        print(f"Using system prompt: {system_prompt_path}")
        self.SYSTEM_PROMPT = system_prompt_path.read_text(encoding="utf-8").strip()


    def answer_question(self, question: str, *, model: str) -> tuple[str, str]:
        """
        檢索相關 chunks，產生 LLM 回答與相似度報表。
        """
        q = question.strip()
        if not q:
            return "", ""
        

        # --- Query rewriting (toggleable) ---
        if ENABLE_QUERY_REWRITE:
            variants = rewrite_query_variants(question, max_variants=REWRITE_MAX_VARIANTS)
        else:
            variants = [question]

        results = []
        for qv in variants:
            q_vec = embed([qv])[0]
            idx, scores = knn_search_switch(
                backend=SEARCH_BACKEND,
                E=self.E,
                index=self.index,
                metas=self.metas,
                q=q_vec,
                k=CANDIDATE_K,
            )
            results.append((idx, scores))

        # Merge candidates across variants (or single query)
        cand_idx, cand_scores = (
            merge_candidates_maxscore(results)
            if len(results) > 1
            else results[0]
        )
        # --- end rewrite ---

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
            model,
            system_prompt=self.SYSTEM_PROMPT,
            user_text=prompt,
            max_retries=2,
        )
        return result_text.strip(), table_str
