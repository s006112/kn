# index_builder.py
import os
import json
import sqlite3
import faiss

from helper_faiss_embedding import embed

SAFE_BATCH = 128


def init_sqlite(path):
    dir_path = os.path.dirname(os.fspath(path))
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    if os.path.exists(path):
        os.remove(path)

    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE chunks(
            vector_id INTEGER PRIMARY KEY,
            chunk_text TEXT,
            metadata_json TEXT
        )
    """)
    return conn

def _progress_bar(done: int, total: int):
    width = 100
    ratio = done / total if total else 1.0
    filled = int(width * ratio)
    bar = "█" * filled + "-" * (width - filled)
    print(f"\r[{bar}] {done}/{total}", end="", flush=True)

def _iter_batches(chunks_path, batch_size):
    batch = []

    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            text = obj.pop("text")

            batch.append((text, obj))

            if len(batch) >= batch_size:
                yield batch
                batch = []

    if batch:
        yield batch


def build_index(chunks_path, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    dim = embed(["test"]).shape[1]
    index = faiss.IndexFlatIP(dim)  # # Using IP on L2-normalized vectors == cosine similarity

    sqlite_path = os.path.join(out_dir, f"{name}_metadata.sqlite")
    conn = init_sqlite(sqlite_path)
    cur = conn.cursor()

    # count total
    with open(chunks_path, encoding="utf-8") as f:
        total = sum(1 for _ in f)

    vid = 0

    for batch in _iter_batches(chunks_path, SAFE_BATCH):
        texts = [t for t, _ in batch]
        metas = [m for _, m in batch]

        embs = embed(texts)
        index.add(embs)

        _progress_bar(vid + len(batch), total)

        for j, meta in enumerate(metas):
            cur.execute(
                "INSERT INTO chunks VALUES (?, ?, ?)",
                (vid, texts[j], json.dumps(meta, ensure_ascii=False))
            )
            vid += 1

    print()

    conn.commit()
    conn.close()

    faiss.write_index(index, os.path.join(out_dir, f"{name}_faiss.index"))
