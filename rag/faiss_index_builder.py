"""faiss_index_builder.py

Responsibility:
Build a FAISS inner-product index and a paired SQLite metadata store from a JSONL chunk file. The module reads chunk records, prepares embedding input text, batches embedding generation, writes vectors into the FAISS index, and stores the original chunk text plus remaining metadata for each vector id.

Used by:
* rag/faiss_build.py

Pipelines:
- jsonl chunks -> batches -> embedding text -> embeddings -> faiss index
- jsonl chunks -> metadata rows -> sqlite -> lookup store

Invariants:
- The SQLite database is recreated before a new build starts.
- Each inserted SQLite row uses the same monotonically increasing vector id used for FAISS insertion order.
- The stored chunk text is the original `text` field from each JSONL record.
- Metadata stored in SQLite excludes the `text` field because it is separated into `chunk_text`.

Out of scope:
- Defining the embedding model or embedding implementation.
- Validating the semantic quality of chunk content or metadata fields.
- Serving search queries or reading back retrieval results.
"""

import os
import json
import sqlite3
import faiss

if __package__:
    from .helper_faiss_embedding import embed
else:
    from helper_faiss_embedding import embed

SAFE_BATCH = 16


def init_sqlite(path):
    """Purpose:
    Create a new SQLite database file with the `chunks` table used for vector metadata storage.
    Inputs:
    - path: Filesystem path for the SQLite database file.
    Outputs:
    - sqlite3.Connection: Open connection to the recreated database.
    """
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
    """Purpose:
    Print an in-place progress bar for index construction progress.
    Inputs:
    - done: Number of processed chunk records.
    - total: Total number of chunk records expected.
    Outputs:
    - None
    """
    width = 100
    ratio = done / total if total else 1.0
    filled = int(width * ratio)
    bar = "█" * filled + "-" * (width - filled)
    print(f"\r[{bar}] {done}/{total}", end="", flush=True)

def _iter_batches(chunks_path, batch_size):
    """Purpose:
    Yield JSONL chunk records as batches of `(text, metadata)` tuples.
    Inputs:
    - chunks_path: Filesystem path to the chunk JSONL file.
    - batch_size: Maximum number of records per yielded batch.
    Outputs:
    - generator[list[tuple[str, dict]]]: Successive batches built from the input file.
    """
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

def build_embedding_text(text: str, meta: dict) -> str:
    """Purpose:
    Assemble the text sent to the embedding model from chunk text and selected metadata.
    Inputs:
    - text: Chunk body text from the JSONL record.
    - meta: Metadata dictionary after removing the `text` field.
    Outputs:
    - str: Embedding input text containing an optional subject title prefix and the chunk body.
    """
    parts = []

    title = meta.get("subject") 
    if title:
        parts.append(f"[TITLE] {title}")

    parts.append(text)

    return "\n".join(parts)


def build_index(chunks_path, out_dir, name):
    """Purpose:
    Build a FAISS index file and matching SQLite metadata database from a chunk JSONL file.
    Inputs:
    - chunks_path: Filesystem path to the chunk JSONL file.
    - out_dir: Directory where output index and SQLite files are written.
    - name: Basename prefix for generated output files.
    Outputs:
    - None
    """
    os.makedirs(out_dir, exist_ok=True)
    dim = embed(["test"]).shape[1]
    index = faiss.IndexFlatIP(dim)

    sqlite_path = os.path.join(out_dir, f"{name}_metadata.sqlite")
    conn = init_sqlite(sqlite_path)
    cur = conn.cursor()

    with open(chunks_path, encoding="utf-8") as f:
        total = sum(1 for _ in f)

    vid = 0

    for batch in _iter_batches(chunks_path, SAFE_BATCH):
        raw_texts = [t for (t, _) in batch]
        embed_texts = [build_embedding_text(t, m) for (t, m) in batch]
        metas = [m for _, m in batch]
        embs = embed(embed_texts)
        index.add(embs)

        _progress_bar(vid + len(batch), total)

        for j, meta in enumerate(metas):
            cur.execute(
                "INSERT INTO chunks VALUES (?, ?, ?)",
                (vid, raw_texts[j], json.dumps(meta, ensure_ascii=False))
            )
            vid += 1

    print()

    conn.commit()
    conn.close()

    faiss.write_index(index, os.path.join(out_dir, f"{name}_faiss.index"))
