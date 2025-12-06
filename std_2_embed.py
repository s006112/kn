#!/usr/bin/env python3
import os
import json
import sqlite3
from glob import glob

import faiss
import torch
from transformers import AutoTokenizer, AutoModel

# ============================================================
# Model: BGE-M3 (MIT license, commercial friendly)
# ============================================================
MODEL_NAME = "BAAI/bge-m3"

print(f"Loading embedding model: {MODEL_NAME}")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModel.from_pretrained(MODEL_NAME).cuda()  # assume GPU exists


def embed(texts):
    """Return L2-normalized dense embeddings for a list of texts."""
    with torch.no_grad():
        tokens = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=2048,  # 足够覆盖大部分条款，避免无谓占用显存
            return_tensors="pt",
        ).to(model.device)

        out = model(**tokens)
        emb = out.last_hidden_state[:, 0]  # CLS pooling
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        return emb.cpu().numpy()


# ============================================================
# Paths
# ============================================================
DATA_DIR = "data/clean_std"
INDEX_DIR = "data/index_std"
os.makedirs(INDEX_DIR, exist_ok=True)


# ============================================================
# SQLite helper
# ============================================================
def init_sqlite(path: str) -> sqlite3.Connection:
    # 每次重建，避免 vector_id 主键冲突
    if os.path.exists(path):
        os.remove(path)

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE chunks (
            vector_id INTEGER PRIMARY KEY,
            doc_type TEXT,
            doc_id TEXT,
            doc_code TEXT,
            location_path TEXT,
            heading TEXT,
            chunk_text TEXT,
            metadata_json TEXT
        )
    """
    )
    conn.commit()
    return conn


# ============================================================
# Load JSONL chunks
# ============================================================
def load_chunks():
    files = glob(os.path.join(DATA_DIR, "*.jsonl"))
    chunks = []
    print(f"Found {len(files)} JSONL files")

    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                text = obj.get("content", "")
                meta = obj.get("metadata", {})
                if text:
                    chunks.append((text, meta))

    print(f"Loaded {len(chunks)} chunks")
    return chunks


# ============================================================
# Simple terminal progress bar (no tqdm)
# ============================================================
def progress_bar(done: int, total: int):
    width = 40
    ratio = done / total if total else 1.0
    filled = int(width * ratio)
    bar = "█" * filled + "-" * (width - filled)
    print(f"\r[{bar}] {done}/{total} ({ratio*100:5.1f}%)", end="", flush=True)


# ============================================================
# Build FAISS index
# ============================================================
def build_index(chunks):
    total = len(chunks)
    if total == 0:
        print("No chunks to index.")
        return

    # 推断向量维度
    dim = embed(["test"]).shape[1]
    index = faiss.IndexFlatIP(dim)

    sqlite_path = os.path.join(INDEX_DIR, "metadata.sqlite")
    conn = init_sqlite(sqlite_path)
    cur = conn.cursor()

    SAFE_BATCH = 32
    vector_id = 0

    for start_idx in range(0, total, SAFE_BATCH):
        batch = chunks[start_idx : start_idx + SAFE_BATCH]
        texts = [t for (t, _) in batch]
        metas = [m for (_, m) in batch]

        embs = embed(texts)

        index.add(embs)

        for j, meta in enumerate(metas):
            meta_json = json.dumps(meta, ensure_ascii=False)
            cur.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    vector_id,
                    meta.get("doc_type"),
                    meta.get("doc_id"),
                    meta.get("doc_code"),
                    meta.get("location_path"),
                    meta.get("heading"),
                    texts[j],
                    meta_json,
                ),
            )
            vector_id += 1

        conn.commit()
        done = min(start_idx + SAFE_BATCH, total)
        progress_bar(done, total)

    print()  # 换行
    faiss.write_index(index, os.path.join(INDEX_DIR, "faiss.index"))
    print("FAISS index + metadata saved.")
    conn.close()


# ============================================================
# Main
# ============================================================
def main():
    chunks = load_chunks()
    build_index(chunks)


if __name__ == "__main__":
    main()
