#!/usr/bin/env python3
"""
Pure brute-force exact kNN for technical standards.
No ANN. 100% recall. Fully auditable.
"""

import sqlite3
import json
import numpy as np
from pathlib import Path

import faiss

from helper.utils_llm import call_llm
from sentence_transformers import SentenceTransformer


class EmbeddingModel:
    def __init__(self, model_name: str, device: str, batch_size: int, task: str = None):
        self.model = SentenceTransformer(model_name, device=device)
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


# ─── Config ─────────────────────────────────────────
DB_PATH = Path("data/index/metadata.sqlite")
INDEX_PATH = Path("data/index/faiss.index")
EMBED_MODEL = "BAAI/bge-m3"
EMBED_BATCH_SIZE = 16
LLM_MODEL = "gpt-4.1-mini"
TOP_K = 20
SYSTEM_PROMPT_PATH = Path("prompt/prompt_rag_system.txt")
SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


# ─── Data Load ─────────────────────────────────────
def load_all_chunks(db_path: Path):
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

        if meta_json:
            try:
                metas.append(json.loads(meta_json))   # ← 正确解析 JSON
            except Exception:
                metas.append({})
        else:
            metas.append({})

    return texts, metas


def load_embedding_matrix(index_path: Path) -> np.ndarray:
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


# ─── Brute-force kNN Core ─────────────────────────
def brute_force_knn(E: np.ndarray, q: np.ndarray, k: int):
    scores = E @ q          # GPU or CPU matrix multiply
    idx = np.argsort(scores)[-k:][::-1]
    return idx, scores[idx]


# ─── Formatting ───────────────────────────────────
def build_similarity_table(top_idx, top_scores, metas, texts):
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
    if not isinstance(meta, dict):
        meta = {}
    doc = meta.get("doc_code", "(doc)")
    loc = meta.get("location_path", "(loc)")
    heading = (meta.get("heading") or "").strip()
    h_part = f" — {heading}" if heading else ""
    return f"[{doc} {loc}{h_part}]\n{text}"


# ─── Main QA ──────────────────────────────────────
def answer_standard_question(question: str):
    if not question.strip():
        return "", ""

    texts, metas = load_all_chunks(DB_PATH)

    embedder = EmbeddingModel(
        model_name=EMBED_MODEL,
        device="cuda:0",
        batch_size=EMBED_BATCH_SIZE,
        task="text_embedding",
    )

    # ─── Load embedding matrix (pre-built) ─────────
    E = load_embedding_matrix(INDEX_PATH)
    if len(texts) != E.shape[0]:
        raise ValueError("Mismatch between metadata rows and embedding matrix size")
    norms = np.linalg.norm(E, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    E = E / norms

    q_vec = embedder.embed_query(question.strip())

    top_idx, top_scores = brute_force_knn(E, q_vec, TOP_K)

    snippets = [format_snippet(texts[i], metas[i]) for i in top_idx]
    context = "\n\n".join(snippets)

    prompt = f"{context}\n\nQuestion: {question.strip()}"

    # Similarity table (log this before LLM call)
    table_str = build_similarity_table(top_idx, top_scores, metas, texts)
    print("\n=== Top hits ===\n")
    print(table_str, flush=True)

    result_text = call_llm(
        LLM_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_text=prompt,
        max_retries=2,
    )

    return result_text.strip(), table_str


# ─── CLI ──────────────────────────────────────────
if __name__ == "__main__":
    q = Path("prompt/prompt_rag_user.txt").read_text(encoding="utf-8")
    answer, sources = answer_standard_question(q)
    print("\n=== Answer ===\n")
    print(answer)
