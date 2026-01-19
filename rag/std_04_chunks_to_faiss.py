#!/usr/bin/env python3
"""
Responsibility:
Build a FAISS vector index and a SQLite metadata table from page-level chunk JSONL
files under `data/standard/json`, using a locally cached HuggingFace BGE-M3 model.

Pipelines:
- jsonl -> chunks -> embeddings -> faiss index -> sqlite metadata -> index files

Invariants:
- Embeddings are L2-normalized and indexed with `faiss.IndexFlatIP`.
- The SQLite database file at `data/standard/index/metadata.sqlite` is rebuilt on
  each run (any existing file is removed).
- Empty lines and chunks with empty `text` are skipped when loading JSONL.

Out of scope:
- Producing or validating the `*.page_blocks.jsonl` inputs.
- Query-time retrieval, reranking, or serving APIs.
"""

import os, json
import sqlite3
from glob import glob
from pathlib import Path
import faiss
import torch
from transformers import AutoTokenizer, AutoModel

# ============================================================
# Model: BGE-M3
# ============================================================
MODEL_PATH = "/root/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

print(f"Loading embedding model: {MODEL_PATH}")
tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
model = AutoModel.from_pretrained(MODEL_PATH, local_files_only=True).cuda()  # assume GPU exists


def embed(texts):
    """
    Purpose:
    Compute dense embeddings for input texts using the loaded transformer model.

    Inputs:
    - texts: List of strings to embed.

    Outputs:
    - Numpy array of shape (len(texts), dim) with L2-normalized vectors.

    Side effects:
    - Runs model inference on the current model device.

    Failure modes:
    - Raises exceptions from tokenization or model execution (e.g., missing model
      files, CUDA/device errors).
    """
    with torch.no_grad():
        tokens = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=2048,  # 足够覆盖大部分頁級內容
            return_tensors="pt",
        ).to(model.device)

        out = model(**tokens)
        emb = out.last_hidden_state[:, 0]  # CLS pooling
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        return emb.cpu().numpy()


# ============================================================
# Paths
# ============================================================
# Input directory containing `*.page_blocks.jsonl` files.
STANDARD_JSON_DIR = Path("data/standard/json")
STANDARD_INDEX_DIR = Path("data/standard/index")
PAGE_BLOCK_SUFFIX = ".page_blocks.jsonl"

os.makedirs(STANDARD_INDEX_DIR, exist_ok=True)


# ============================================================
# SQLite helper
# ============================================================
def init_sqlite(path: str) -> sqlite3.Connection:
    """
    Purpose:
    Create a new SQLite database containing a `chunks` table for vector metadata.

    Inputs:
    - path: Filesystem path for the SQLite database file.

    Outputs:
    - An open `sqlite3.Connection` with the schema created.

    Side effects:
    - Removes `path` if it already exists.
    - Creates and writes a SQLite file on disk.

    Failure modes:
    - Raises `OSError`/`sqlite3.Error` on filesystem or database errors.
    """
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
# Load page-level blocks
# ============================================================
def load_chunks():
    """
    Purpose:
    Load page-level text blocks from `*.page_blocks.jsonl` files and map them to
    `(text, metadata)` tuples suitable for indexing and SQLite storage.

    Inputs:
    - None (reads files from `STANDARD_JSON_DIR` matching `PAGE_BLOCK_SUFFIX`).

    Outputs:
    - List of `(text, meta)` tuples where `meta` is a dict containing fixed
      fields used by the SQLite schema plus original block identifiers.

    Side effects:
    - Reads JSONL files from disk.
    - Prints basic progress and counts to stdout.

    Failure modes:
    - Raises exceptions from file I/O or `json.loads` for malformed inputs.
    """
    pattern = os.path.join(STANDARD_JSON_DIR, f"*{PAGE_BLOCK_SUFFIX}")
    files = glob(pattern)
    chunks = []

    print(f"Looking for page_blocks: {pattern}")
    print(f"Found {len(files)} JSONL files")

    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)

                text = obj.get("text", "").strip()
                if not text:
                    continue

                file_id = obj.get("file_id")
                page = obj.get("page", 0)
                block_id = obj.get("block_id")

                # 給 SQLite 那幾個固定欄位一個合理映射
                meta = {
                    "doc_type": "page_block",
                    "doc_id": file_id,                  # 可以理解成檔案 ID
                    "doc_code": file_id,                # Keep doc_code identical to doc_id for traceability.
                    "location_path": f"page:{page}",    # 粗略定位
                    "heading": None,                    # Page blocks do not provide headings in this pipeline.
                    # 其餘信息原樣保留
                    "block_id": block_id,
                    "page": page,
                    "char": obj.get("char"),
                    "word": obj.get("word"),
                }

                chunks.append((text, meta))

    print(f"Loaded {len(chunks)} chunks")
    return chunks


# ============================================================
# Simple terminal progress bar (no tqdm)
# ============================================================
def progress_bar(done: int, total: int):
    """
    Purpose:
    Print an in-place progress bar to stdout.

    Inputs:
    - done: Completed item count.
    - total: Total item count.

    Outputs:
    - None.

    Side effects:
    - Writes to stdout without a trailing newline.

    Failure modes:
    - None (best-effort display).
    """
    width = 40
    ratio = done / total if total else 1.0
    filled = int(width * ratio)
    bar = "█" * filled + "-" * (width - filled)
    print(f"\r[{bar}] {done}/{total} ({ratio*100:5.1f}%)", end="", flush=True)


# ============================================================
# Build FAISS index
# ============================================================
def build_index(chunks):
    """
    Purpose:
    Build a FAISS inner-product index over chunk embeddings and persist both the
    FAISS index and per-vector metadata.

    Inputs:
    - chunks: List of `(text, meta)` tuples as returned by `load_chunks()`.

    Outputs:
    - None.

    Side effects:
    - Computes embeddings via `embed()`.
    - Writes `faiss.index` and `metadata.sqlite` under `STANDARD_INDEX_DIR`.
    - Prints progress to stdout.

    Failure modes:
    - Raises exceptions from embedding/model execution, FAISS, SQLite, or file I/O.
    """
    total = len(chunks)
    if total == 0:
        print("No chunks to index.")
        return

    # 推断向量维度
    dim = embed(["test"]).shape[1]
    index = faiss.IndexFlatIP(dim)

    sqlite_path = os.path.join(STANDARD_INDEX_DIR, "metadata.sqlite")
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
    faiss.write_index(index, os.path.join(STANDARD_INDEX_DIR, "faiss.index"))
    print("FAISS index + metadata saved.")
    conn.close()


# ============================================================
# Main
# ============================================================
def main():
    """
    Purpose:
    Load chunks from disk and build the FAISS + SQLite outputs.

    Inputs:
    - None.

    Outputs:
    - None.

    Side effects:
    - Reads JSONL inputs, runs embedding inference, and writes index artifacts.

    Failure modes:
    - Propagates exceptions from `load_chunks()` and `build_index()`.
    """
    chunks = load_chunks()
    build_index(chunks)


if __name__ == "__main__":
    main()
