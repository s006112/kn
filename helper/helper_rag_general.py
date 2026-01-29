# helper_rag_general.py
#!/usr/bin/env python3
"""
Responsibility:
Implements a self-contained RAG engine (`RagEngine`) for the local `"standard"` and `"mbox"` indexes: loads chunk text/metadata from SQLite, reconstructs an in-memory embedding matrix from a FAISS index, embeds queries, performs brute-force top-k similarity search, and calls an LLM with retrieved context.

Used by:
* ali_email/ali_llm.py
* rag/email_03b_web_gui.py

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

Step 2 (NEXT):
- Add MMR (Maximal Marginal Relevance) reranking.
  - Purpose: diversify retrieved chunks and reduce redundancy.
  - Pipeline:
    brute_force_knn -> candidate_pool -> MMR selection.

Step 3:
- Add email_id grouping semantics.
  - Treat chunks with the same metadata["email_id"] as belonging to one logical document.
  - Ensure top results represent distinct emails, not isolated chunks.

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

# Assume these helpers are in place
from helper.utils_llm import call_llm
from helper.helper_embedding import embed


# ─── Config (保持原有的配置) ────────────────────────────────
EMBED_BATCH_SIZE = 16
LLM_MODEL = "sonar"
TOP_K = 10
CANDIDATE_K = 50
SCORE_THRESHOLD = 0.5
SYSTEM_PROMPT_PATH = Path("prompt/prompt_rag_system.txt")

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
    faiss_dir = Path("data/faiss")
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


# ─── Helper Classes/Functions (保留在內部) ────────────────

class EmbeddingModel:
    """
    Responsibility:
    Thin wrapper around `helper.helper_embedding.embed` for producing L2-normalized query embeddings.
    """

    def __init__(
        self,
        model_name: str | None = None,
        device: str = "",
        batch_size: int = 0,
        task: str | None = None,
    ):
        """
        Purpose:
        Initialize a lightweight wrapper for query embedding.

        Inputs:
        - model_name: Model name (kept for API compatibility; unused).
        - device: Target device string (kept for API compatibility; unused).
        - batch_size: Batch size (kept for API compatibility; stored only).
        - task: Optional task parameter (kept for API compatibility; unused).

        Outputs:
        - None.

        Side effects:
        - None. The underlying embedding model is loaded lazily on first call to `embed_query`.

        Failure modes:
        - None.
        """
        self.batch_size = batch_size
        # Model is loaded lazily in helper_embedding

    def embed_query(self, text):
        """
        Purpose:
        Embed a single query string into a normalized vector.

        Inputs:
        - text: Query string.

        Outputs:
        - 1D NumPy array embedding.

        Side effects:
        - Runs the underlying embedding model.
        - May lazily load model weights and allocate accelerator memory on the first call.

        Failure modes:
        - Propagates exceptions from helper_embedding.embed.
        """
        return embed([text])[0]


# 所有的 load/knn/format 函數都移到這裡作為內部函數 (不公開，但 RagEngine 會調用)
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
            "SELECT vector_id, chunk_text, metadata_json FROM chunks ORDER BY vector_id"
        ).fetchall()
    finally:
        conn.close()

    texts, metas = [], []
    for _vid, text, meta_json in rows:
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


def mmr_select(E: np.ndarray, candidate_idx, q: np.ndarray, k: int, lambda_: float):
    """
    Purpose:
    Select a diverse subset of indices using Maximal Marginal Relevance (MMR).

    Inputs:
    - E: Embedding matrix shaped `(n, d)` (L2-normalized rows).
    - candidate_idx: 1D iterable of candidate row indices.
    - q: Query vector shaped `(d,)` (L2-normalized).
    - k: Number of selections to return.
    - lambda_: MMR tradeoff parameter in [0, 1].

    Outputs:
    - List of selected indices, length `min(k, len(candidate_idx))`.

    Side effects:
    - None.
    """
    cand = np.asarray(candidate_idx, dtype=int)
    if cand.size == 0 or k <= 0:
        return []

    k = min(k, cand.size)
    q_scores = E[cand] @ q
    selected = []
    selected_mask = np.zeros(cand.size, dtype=bool)

    for _ in range(k):
        if not selected:
            pick_pos = int(np.argmax(q_scores))
        else:
            remaining_pos = np.where(~selected_mask)[0]
            if remaining_pos.size == 0:
                break
            sim_to_selected = E[cand[remaining_pos]] @ E[np.asarray(selected, dtype=int)].T
            if sim_to_selected.ndim == 1:
                max_sim = sim_to_selected
            else:
                max_sim = sim_to_selected.max(axis=1)
            mmr_scores = lambda_ * q_scores[remaining_pos] - (1.0 - lambda_) * max_sim
            pick_pos = int(remaining_pos[np.argmax(mmr_scores)])

        selected_mask[pick_pos] = True
        selected.append(int(cand[pick_pos]))

    return selected


def _build_similarity_table(top_idx, top_scores, metas, *, page_key: str):
    """
    Purpose:
    Build a Markdown table summarizing retrieval scores and selected metadata fields.

    Inputs:
    - top_idx: 1D iterable of row indices selected from `metas`.
    - top_scores: 1D iterable of similarity scores aligned with `top_idx`.
    - metas: List of metadata dicts aligned to the embedding matrix rows.
    - page_key: Metadata key to use for the "page" column (e.g. `"page"`).

    Outputs:
    - A Markdown string containing a table and a total word count line.

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions if `top_idx` contains out-of-range indices.
    """
    idx = np.asarray(top_idx, dtype=int)
    scores = np.asarray(top_scores, dtype=float)
    if scores.size and idx.size:
        n = min(idx.size, scores.size)
        idx = idx[:n]
        scores = scores[:n]
        order = np.argsort(scores)[::-1]
        idx = idx[order]
        scores = scores[order]

    table = [
        "| score | doc | date | doc_type | page | word |",
        "|---:|---|---|---|---:|---:|",
    ]
    total_words = 0
    for i, s in zip(idx, scores):
        meta = metas[i] or {}
        doc = meta.get("subject") or meta.get("doc_id")     # 顯示用：優先 subject，其次才用 doc_id
        doc_date = meta.get("date")
        doc_type = meta.get("doc_type")
        page = meta.get(page_key)
        word_count = meta.get("word", 0) or 0
        total_words += int(word_count)
        table.append(f"| {float(s):.4f} | {doc} | {doc_date} | {doc_type} | {page} | {word_count} |")
    table.append(f"Total word count: {total_words}")
    return "\n".join(table)


# ─── 核心類別 ──────────────────────────────
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

        self.mode = mode
        db_path, index_path = get_faiss_artifact_paths(mode)
        print("Initializing RagEngine: Loading FAISS index and Embedding Model...")
        self.texts, self.metas = _load_all_chunks(db_path)
        self.embedder = EmbeddingModel(
            device="cuda:0", # Use original device setting
            batch_size=EMBED_BATCH_SIZE,
        )
        self.E = _load_embedding_matrix(index_path)
        if len(self.texts) != self.E.shape[0]:
            raise ValueError("Mismatch between metadata rows and embedding matrix size")
        
        # Normalize E once upon load
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

        q_vec = self.embedder.embed_query(q)
        cand_idx, cand_scores = brute_force_knn(self.E, q_vec, CANDIDATE_K)
        top_idx = mmr_select(self.E, cand_idx, q_vec, TOP_K, lambda_=0.5)
        top_scores = (self.E[top_idx] @ q_vec)
        keep_mask = top_scores >= SCORE_THRESHOLD
        if not np.any(keep_mask):
            table_str = _build_similarity_table(cand_idx, cand_scores, self.metas, page_key="page")
            return "", table_str
        top_idx = np.asarray(top_idx)[keep_mask]
        top_scores = top_scores[keep_mask]

        # 直接使用原始文本作為 snippet，前綴已在 `rag/std_03_txt_to_chunks.py` 中注入
        snippets = [self.texts[i] for i in top_idx]
        context = "\n\n".join(snippets)

        prompt = f"{context}\n\nQuestion: {q}"
        table_str = _build_similarity_table(cand_idx, cand_scores, self.metas, page_key="page")
        
        result_text = call_llm(
            LLM_MODEL,
            system_prompt=self.SYSTEM_PROMPT,
            user_text=prompt,
            max_retries=2,
        )

        return result_text.strip(), table_str
