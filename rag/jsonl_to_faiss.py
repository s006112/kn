#!/usr/bin/env python3
"""
Responsibility:
Build a FAISS vector index and a SQLite metadata table from page-level chunk JSONL
files under `data/{TARGET_CHUNK_FOLDER}/json`, using a locally cached HuggingFace BGE-M3 model.

Pipelines:
- jsonl -> chunks -> embeddings -> faiss index -> sqlite metadata -> index files

Invariants:
- Embeddings are L2-normalized and indexed with `faiss.IndexFlatIP`.
- The SQLite database file at `data/faiss/{TARGET_CHUNK_FOLDER}_metadata.sqlite` is rebuilt on
  each run (any existing file is removed).
- Empty lines and chunks with empty `text` are skipped when loading JSONL.

Out of scope:
- Producing or validating the `*.page_blocks.jsonl` inputs.
- Query-time retrieval, reranking, or serving APIs.
"""

import os, json, sys
import sqlite3
from glob import glob
from pathlib import Path
import faiss

# Add project root to sys.path for helper imports
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_embedding import embed

TARGET_CHUNK_FOLDER = "mbox"  #  mbox or standard

JSON_DIR = Path(f"data/{TARGET_CHUNK_FOLDER}/jsonl")
FAISS_DIR = Path("data/faiss")
BLOCK_SUFFIX = "blocks.jsonl"

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
            chunk_text TEXT,
            metadata_json TEXT
        )
    """
    )
    conn.commit()
    return conn


def load_chunks():
    """
    Purpose:
    Load page-level text blocks from `*.page_blocks.jsonl` files and map them to
    `(text, metadata)` tuples suitable for indexing and SQLite storage.

    Inputs:
    - None (reads files from `JSON_DIR` matching `BLOCK_SUFFIX`).

    Outputs:
    - List of `(text, meta)` tuples where `meta` is a dict containing fixed
      fields used by the SQLite schema plus original block identifiers.

    Side effects:
    - Reads JSONL files from disk.
    - Prints basic progress and counts to stdout.

    Failure modes:
    - Raises exceptions from file I/O or `json.loads` for malformed inputs.
    """
    pattern = os.path.join(JSON_DIR, f"*{BLOCK_SUFFIX}")
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
                if TARGET_CHUNK_FOLDER == "mbox":
                    if obj.get("part") == "body":
                        continue

                text = (obj.get("text") or obj.get("content") or "").strip()
                if not text:
                    continue

                if TARGET_CHUNK_FOLDER == "standard":
                    meta = {
                        # ── general schema fields ──
                        "doc_type": obj.get("file_type"),
                        "doc_id": obj.get("doc_id"),  # 可以理解成檔案 ID
                        "chunk_id": obj.get("block_id"),
                        "page": obj.get("page"),
                        "char": obj.get("char"),
                        "word": obj.get("word"),

                        # ── standard native fields ──
                        "source": obj.get("source"),
                    }                    

                elif TARGET_CHUNK_FOLDER == "mbox":
                    meta = {
                        # ── general schema fields ──
                        "doc_type": obj.get("file_type"),
                        "doc_id": obj.get("email_id"), 
                        "chunk_id": obj.get("block_id"),
                        "page": obj.get("page"),
                        "char": obj.get("char"),
                        "word": obj.get("word"),

                        # ── email native fields ──
                        "subject": obj.get("subject"),          # 人类可读标题（显示用）
                        "thread_id": obj.get("thread_id"),
                        "from": obj.get("from"),
                        "to": obj.get("to"),
                        "date": obj.get("date"),
                        #"part": obj.get("part"),  # redundant
                        #"attachment_type": obj.get("file_type"),    # duplicate
                        #"attachment_name": obj.get("attachment"),   # redundant
                    }

                chunks.append((text, meta))

    print(f"Loaded {len(chunks)} chunks")
    return chunks


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
    width = 100
    ratio = done / total if total else 1.0
    filled = int(width * ratio)
    bar = "█" * filled + "-" * (width - filled)
    print(f"\r[{bar}] {done}/{total} ({ratio*100:5.1f}%)", end="", flush=True)


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
    - Writes `faiss.index` and `metadata.sqlite` under `FAISS_DIR`.
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

    sqlite_path = os.path.join(FAISS_DIR, f"{TARGET_CHUNK_FOLDER}_metadata.sqlite")
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
                "INSERT INTO chunks VALUES (?, ?, ?)",
                (
                    vector_id,
                    texts[j],
                    meta_json,
                ),
            )
            vector_id += 1

        conn.commit()
        done = min(start_idx + SAFE_BATCH, total)
        progress_bar(done, total)

    print()  # 换行
    faiss.write_index(index, os.path.join(FAISS_DIR, f"{TARGET_CHUNK_FOLDER}_faiss.index"))
    print("FAISS index + metadata saved.")
    conn.close()

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
