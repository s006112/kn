# index_builder.py
import os
import json
import sqlite3
import faiss

from helper.helper_faiss_embedding import embed

SAFE_BATCH = 16


def init_sqlite(path):
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


def build_index(chunks, out_dir: str, name: str):
    dim = embed(["test"]).shape[1]
    index = faiss.IndexFlatIP(dim)

    sqlite_path = os.path.join(out_dir, f"{name}_metadata.sqlite")
    conn = init_sqlite(sqlite_path)
    cur = conn.cursor()

    vid = 0
    total = len(chunks)

    for i in range(0, total, SAFE_BATCH):
        batch = chunks[i:i+SAFE_BATCH]

        texts = [t for t, _ in batch]
        metas = [m for _, m in batch]

        embs = embed(texts)
        index.add(embs)

        _progress_bar(min(i + SAFE_BATCH, total), total)

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
    print("FAISS index + metadata saved.")
