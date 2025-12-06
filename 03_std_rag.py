#!/usr/bin/env python3
"""
03_std_rag_bruteforce.py
Pure brute-force exact kNN for technical standards.
No FAISS. No ANN. 100% recall. Fully auditable.
"""

import sqlite3
import json
import numpy as np
from pathlib import Path

from rag.rag_embeddings import EmbeddingModel
from helper.utils_llm import call_llm

from sentence_transformers import SentenceTransformer
import numpy as np


class EmbeddingModel:
    def __init__(self, model_name: str, device: str, batch_size: int, task: str = None):
        self.model = SentenceTransformer(model_name, device=device)
        self.batch_size = batch_size

    def embed_documents(self, texts):
        return self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

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
DB_PATH = Path("data/index_std/metadata.sqlite")
EMBED_MODEL = "BAAI/bge-m3"
EMBED_BATCH_SIZE = 16
LLM_MODEL = "gpt-4.1-mini"
TOP_K = 20


# ─── Data Load ─────────────────────────────────────
def load_all_chunks(db_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT vector_id, chunk_text, metadata_json FROM chunks"
        ).fetchall()
    finally:
        conn.close()

    ids, texts, metas = [], [], []
    for vid, text, meta_json in rows:
        ids.append(int(vid))
        texts.append(text)

        if meta_json:
            try:
                metas.append(json.loads(meta_json))   # ← 正确解析 JSON
            except Exception:
                metas.append({})
        else:
            metas.append({})

    return ids, texts, metas


# ─── Brute-force kNN Core ─────────────────────────
def brute_force_knn(E: np.ndarray, q: np.ndarray, k: int):
    scores = E @ q          # GPU or CPU matrix multiply
    idx = np.argsort(scores)[-k:][::-1]
    return idx, scores[idx]


# ─── Formatting ───────────────────────────────────
def format_snippet(text: str, meta: dict) -> str:
    doc = meta.get("doc_code", "(doc)")
    loc = meta.get("location_path", "(loc)")
    heading = meta.get("heading", "").strip()
    h_part = f" — {heading}" if heading else ""
    return f"[{doc} {loc}{h_part}]\n{text}"


# ─── Main QA ──────────────────────────────────────
def answer_standard_question(question: str):
    if not question.strip():
        return "", ""

    ids, texts, metas = load_all_chunks(DB_PATH)

    embedder = EmbeddingModel(
        model_name=EMBED_MODEL,
        device="cuda:0",
        batch_size=EMBED_BATCH_SIZE,
        task="text_embedding",
    )

    # ─── Build embedding matrix (full brute-force) ─────────
    E = np.asarray(embedder.embed_documents(texts), dtype=np.float32)
    norms = np.linalg.norm(E, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    E = E / norms

    q_vec = embedder.embed_query(question.strip())

    top_idx, top_scores = brute_force_knn(E, q_vec, TOP_K)

    snippets = [format_snippet(texts[i], metas[i]) for i in top_idx]
    context = "\n\n".join(snippets)

    prompt = f"""Refer to the following clauses and answer the question with citations to clause numbers:

{context}

Question: {question.strip()}
"""

    result_text = call_llm(
        LLM_MODEL,
        system_prompt="You are a technical standards assistant. Cite clause numbers and table IDs in your answers.",
        user_text=prompt,
        max_retries=2,
    )

    # Similarity table
    table = ["| score | doc | clause | heading |", "|---:|---|---|---|"]
    for i, s in zip(top_idx, top_scores):
        meta = metas[i]
        table.append(
            f"| {float(s):.4f} | {meta.get('doc_code')} | {meta.get('location_path')} | {meta.get('heading','')} |"
        )

    return result_text.strip(), "\n".join(table)


# ─── CLI ──────────────────────────────────────────
if __name__ == "__main__":
    q = "一款仅适用于橱柜底部安装的 under-cabinet 灯具，根据 UL 1598，铭牌或标签上必须有哪一句或哪些安装适用性标示？这些标示文字在哪一个条款和哪一张表格中规定？Only reply in Chinese"
    answer, sources = answer_standard_question(q)
    print("\n=== Answer ===\n")
    print(answer)
    print("\n=== Top hits ===\n")
    print(sources)
