# helper_rag_worker.py
#!/usr/bin/env python3
"""
helper_rag_worker.py

Encapsulated RAG core logic (RagEngine).
Heavy resources (FAISS, SentenceTransformer) are only loaded upon instantiation.
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
DB_PATH = Path("data/index/metadata.sqlite")
INDEX_PATH = Path("data/index/faiss.index")
EMBED_MODEL = "BAAI/bge-m3"
EMBED_BATCH_SIZE = 16
LLM_MODEL = "sonar-reasoning-pro"
TOP_K = 30
SYSTEM_PROMPT_PATH = Path("prompt/prompt_rag_system.txt")


# ─── Helper Classes/Functions (保留在內部) ────────────────

class EmbeddingModel:
    # 保持原有的 EmbeddingModel 類
    def __init__(self, model_name: str, device: str, batch_size: int, task: str = None):
        actual_device = device
        if device.startswith("cuda") and not torch.cuda.is_available():
            print("CUDA not available, falling back to CPU for embeddings.")
            actual_device = "cpu"
        self.model = SentenceTransformer(model_name, device=actual_device)
        self.batch_size = batch_size

    def embed_query(self, text):
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
    # (實現與 20_rag.py 中 load_all_chunks 相同)
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
    # (實現與 20_rag.py 中 load_embedding_matrix 相同)
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
    # (實現與 20_rag.py 中 brute_force_knn 相同)
    scores = E @ q
    idx = np.argsort(scores)[-k:][::-1]
    return idx, scores[idx]


def build_similarity_table(top_idx, top_scores, metas, texts):
    # (實現與 20_rag.py 中 build_similarity_table 相同)
    table = ["| score | doc | page | text |", "|---:|---|---|---|"]
    for i, s in zip(top_idx, top_scores):
        meta = metas[i] or {}
        doc = meta.get("doc_code")
        page = meta.get("page")
        full_text = texts[i] or ""
        preview = full_text[:10]
        if len(full_text) > 10:
            preview += "...."
        table.append(f"| {float(s):.4f} | {doc} | {page} | {preview} |")
    return "\n".join(table)


def format_snippet(text: str, meta: dict) -> str:
    # (實現與 20_rag.py 中 format_snippet 相同)
    if not isinstance(meta, dict):
        meta = {}
    doc = meta.get("doc_code", "(doc)")
    loc = meta.get("location_path", "(loc)")
    heading = (meta.get("heading") or "").strip()
    h_part = f" — {heading}" if heading else ""
    return f"[{doc} {loc}{h_part}]\n{text}"


# ─── 核心類別 ──────────────────────────────
class RagEngine:
    """Encapsulates RAG initialization and query logic."""
    def __init__(self):
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
        Performs the RAG pipeline. 
        Returns: (LLM Answer, Source Table String for debugging/logging)
        """
        q = question.strip()
        if not q:
            return "", ""

        q_vec = self.embedder.embed_query(q)
        top_idx, top_scores = brute_force_knn(self.E, q_vec, TOP_K)

        snippets = [format_snippet(self.texts[i], self.metas[i]) for i in top_idx]
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
