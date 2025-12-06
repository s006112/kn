#!/usr/bin/env python3
import os
import json
import sqlite3
import faiss
import torch
import time
from transformers import AutoTokenizer, AutoModel
from glob import glob

# ============================================================
# Model: BGE-M3 (MIT license, commercial friendly)
# ============================================================
MODEL_NAME = "BAAI/bge-m3"

print(f"Loading embedding model: {MODEL_NAME}")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModel.from_pretrained(MODEL_NAME).cuda()  # assume GPU exists

def embed(texts):
    """Return L2-normalized dense embeddings (batch capable)."""
    with torch.no_grad():
        tokens = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=8192,
            return_tensors="pt"
        ).to(model.device)

        out = model(**tokens)
        emb = out.last_hidden_state[:, 0]              # CLS pooling
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        return emb.cpu().numpy()

# ============================================================
# I/O paths
# ============================================================
DATA_DIR = "data/clean_std"
INDEX_DIR = "data/index_std"
os.makedirs(INDEX_DIR, exist_ok=True)

def log_batch_status(batch_idx, total_batches, batch_size, elapsed):
    print(f"   🔍 Embedding batch of {batch_size} chunks...{batch_idx}/{total_batches}")
    print(f"   ✅ Embedding done in {elapsed:.2f}s")


# ============================================================
# SQLite helper
# ============================================================
def init_sqlite(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            vector_id INTEGER PRIMARY KEY,
            doc_type TEXT,
            doc_id TEXT,
            doc_code TEXT,
            location_path TEXT,
            heading TEXT,
            chunk_text TEXT,
            metadata_json TEXT
        )
    """)
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
                if not line.strip():
                    continue
                obj = json.loads(line)
                text = obj.get("content", "")
                meta = obj.get("metadata", {})
                chunks.append((text, meta))
    print(f"Loaded {len(chunks)} chunks")
    return chunks

# ============================================================
# Build FAISS index
# ============================================================
def build_index(chunks):
    # Embedding dimensions for BGE-M3 ~1024 (CLS)
    test_emb = embed(["test"])
    dim = test_emb.shape[1]

    index = faiss.IndexFlatIP(dim)  # cosine = dot product on L2-normalized vectors
    sqlite_path = os.path.join(INDEX_DIR, "metadata.sqlite")
    conn = init_sqlite(sqlite_path)
    cur = conn.cursor()

    vector_id = 0
    batch = []
    metas = []

    # Batch embedding for throughput
    BATCH_SIZE = 10  # 你原本是 500，所以沿用
    total_batches = (len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE
    batch_index = 0

    for text, meta in chunks:
        batch.append(text)
        metas.append(meta)

        if len(batch) == BATCH_SIZE:
            batch_index += 1
            start = time.time()

            embs = embed(batch)
            elapsed = time.time() - start
            log_batch_status(batch_index, total_batches, len(batch), elapsed)

            index.add(embs)

            for i, embedding in enumerate(embs):
                meta_json = json.dumps(metas[i], ensure_ascii=False)
                cur.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        vector_id,
                        meta.get("doc_type"),
                        meta.get("doc_id"),
                        meta.get("doc_code"),
                        meta.get("location_path"),
                        meta.get("heading"),
                        batch[i],
                        meta_json,
                    ),
                )
                vector_id += 1

            conn.commit()
            batch, metas = [], []

    # Last small batch
    if batch:
        batch_index += 1
        start = time.time()

        embs = embed(batch)
        elapsed = time.time() - start
        log_batch_status(batch_index, total_batches, len(batch), elapsed)

        index.add(embs)
        for i, embedding in enumerate(embs):
            meta = metas[i]
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
                    batch[i],
                    meta_json,
                ),
            )
            vector_id += 1
        conn.commit()

    # Save index
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
