# helper_rag_worker.py
#!/usr/bin/env python3
"""
Responsibility:
Implements a self-contained RAG engine (`RagEngine`) for the standard document index: loads chunk text/metadata from SQLite, loads FAISS embeddings, embeds queries, performs a KNN-style search, and calls an LLM with retrieved context.

Used by:
* ali_email/ali_llm.py
* tool/test_std_rag.py

Pipelines:
- load_chunks -> load_index -> normalize_vectors -> embed_query -> knn_search -> build_prompt -> call_llm

Invariants:
- Embedding matrix `E` is L2-normalized once after loading.
- `answer_question` returns `("", "")` for empty/whitespace-only questions.
- Raises when the number of loaded texts does not match the FAISS vector count.

Out of scope:
- Building the FAISS index and populating the SQLite metadata store.
- Chunking/sanitization of source documents.
"""
from __future__ import annotations

import sqlite3
import json
import numpy as np
import faiss
import torch
from pathlib import Path
from typing import Optional, Any, Dict, List, Tuple

# Assume these helpers are in place
from helper.utils_llm import call_llm
from sentence_transformers import SentenceTransformer


# ─── Config (保持原有的配置) ────────────────────────────────
# 由於這些是常數，可以保留在這裡
DB_PATH = Path("data/faiss/standard_metadata.sqlite")
INDEX_PATH = Path("data/faiss/standard_faiss.index")
EMBED_MODEL = "BAAI/bge-m3"
EMBED_BATCH_SIZE = 16
LLM_MODEL = "sonar"
TOP_K = 10
SYSTEM_PROMPT_PATH = Path("prompt/prompt_rag_system.txt")


# ─── Helper Classes/Functions (保留在內部) ────────────────

class EmbeddingModel:
    """
    Responsibility:
    Thin wrapper around `SentenceTransformer` to produce a normalized query embedding.
    """

    def __init__(self, model_name: str, device: str, batch_size: int, task: str = None):
        """
        Purpose:
        Initialize a sentence-transformers model on the requested device with a CPU fallback.

        Inputs:
        - model_name: SentenceTransformer model name or local path.
        - device: Target device string (e.g. `"cpu"`, `"cuda"`, `"cuda:0"`).
        - batch_size: Batch size retained for API compatibility; used by this wrapper as stored config.
        - task: Optional task parameter (currently unused by this wrapper).

        Outputs:
        - None.

        Side effects:
        - Loads model weights and may allocate GPU memory.
        - Prints a notice when CUDA is requested but unavailable.

        Failure modes:
        - Propagates exceptions from `SentenceTransformer` initialization.
        """

        actual_device = device
        if device.startswith("cuda") and not torch.cuda.is_available():
            print("CUDA not available, falling back to CPU for embeddings.")
            actual_device = "cpu"
        self.model = SentenceTransformer(model_name, device=actual_device)
        self.batch_size = batch_size

    def embed_query(self, text):
        """
        Purpose:
        Embed a single query string into a normalized vector.

        Inputs:
        - text: Query string.

        Outputs:
        - 1D NumPy array embedding.

        Side effects:
        - Runs the underlying transformer model.

        Failure modes:
        - Propagates exceptions from `SentenceTransformer.encode`.
        """

        v = self.model.encode(
            [text],
            batch_size=1,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return v[0]


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

    Side effects:
    - None.

    Failure modes:
    - Propagates NumPy shape errors when `E` and `q` are incompatible.
    """

    scores = E @ q
    idx = np.argsort(scores)[-k:][::-1]
    return idx, scores[idx]


def build_similarity_table(top_idx, top_scores, metas, texts):
    """
    Purpose:
    Build a Markdown table summarizing retrieved chunks with scores and selected metadata fields.

    Inputs:
    - top_idx: Iterable of selected indices into `metas`/`texts`.
    - top_scores: Iterable of scores aligned to `top_idx`.
    - metas: List of metadata dicts.
    - texts: List of chunk texts (currently unused by this formatter).

    Outputs:
    - Markdown string containing a table plus a trailing total-word-count line.

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions if indices are out of range or metadata is not dict-like.
    """

    table = ["| score | doc | page | word |", "|---:|---|---:|---:|"]
    total_words = 0
    for i, s in zip(top_idx, top_scores):
        meta = metas[i] or {}
        doc = meta.get("doc_code")
        page = meta.get("page")
        # 從 metadata 中直接讀取 word 數
        word_count = meta.get("word", 0) or 0
        total_words += int(word_count)
        table.append(f"| {float(s):.4f} | {doc} | {page} | {word_count} |")
    # 在表格之後加一行總詞數
    table.append(f"Total word count: {total_words}")
    return "\n".join(table)


# ─── 核心類別 ──────────────────────────────
class RagEngine:
    """
    Responsibility:
    Encapsulates RAG initialization (loading chunks/index/model) and query-time retrieval + LLM answering.
    """

    def __init__(self):
        """
        Purpose:
        Load chunks from SQLite, load vectors from FAISS, normalize vectors, and load the system prompt.

        Inputs:
        - None.

        Outputs:
        - None.

        Side effects:
        - Loads FAISS index vectors into memory.
        - Loads an embedding model (may allocate GPU memory).
        - Reads a prompt file from disk.
        - Prints an initialization message.

        Failure modes:
        - Raises on DB/index read failures.
        - Raises `ValueError` when the loaded chunk count does not match vector count.
        - Propagates filesystem errors when reading the system prompt fails.
        """

        print("Initializing RagEngine: Loading FAISS index and Embedding Model...")
        self.texts, self.metas = _load_all_chunks(DB_PATH)
        self.embedder = EmbeddingModel(
            model_name=EMBED_MODEL,
            device="cuda:0", # Use original device setting
            batch_size=EMBED_BATCH_SIZE,
        )
        self.E = _load_embedding_matrix(INDEX_PATH)
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
        top_idx, top_scores = brute_force_knn(self.E, q_vec, TOP_K)

        # 直接使用原始文本作為 snippet，前綴已在 `rag/std_03_txt_to_chunks.py` 中注入
        snippets = [self.texts[i] for i in top_idx]
        context = "\n\n".join(snippets)

        prompt = f"{context}\n\nQuestion: {q}"
        table_str = build_similarity_table(top_idx, top_scores, self.metas, self.texts)
        
        result_text = call_llm(
            LLM_MODEL,
            system_prompt=self.SYSTEM_PROMPT,
            user_text=prompt,
            max_retries=2,
        )

        return result_text.strip(), table_str
