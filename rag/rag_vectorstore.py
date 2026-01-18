"""
Responsibility:
Defines a minimal vector store abstraction and a FAISS-backed implementation that persists vectors to `vectors.faiss` and metadata to `metadata.sqlite`.

Used by:
* rag/email_02_chunks_to_faiss.py
* rag/email_03_web_gui.py
* rag/rag_retrieval.py

Pipelines:
- add_embeddings -> write_faiss -> write_sqlite
- read_faiss -> read_sqlite -> build_chunks
- archive_files -> write_new_index

Invariants:
- Vector IDs are assigned incrementally starting from 0 within a `FaissStore` instance.
- Metadata is persisted as JSON text in SQLite and re-hydrated on load.
- `load_faiss_index_and_metadata` returns a `docs_by_id` mapping keyed by the stored vector ID.

Out of scope:
- Embedding generation and text chunking.
- Retrieval/scoring policies beyond storing and loading data.
"""

import json
import sqlite3
import time
from pathlib import Path

import faiss
import numpy as np


class Chunk:
    """
    Responsibility:
    Container for a text chunk and its metadata used by retrieval and UI layers.
    """

    __slots__ = ("page_content", "metadata")

    def __init__(self, page_content: str, metadata: dict):
        """
        Purpose:
        Create a chunk record.

        Inputs:
        - page_content: Chunk text payload.
        - metadata: Arbitrary JSON-serializable metadata dict.

        Outputs:
        - None.

        Side effects:
        - None.

        Failure modes:
        - None.
        """

        self.page_content = page_content
        self.metadata = metadata


class VectorStore:
    """
    Responsibility:
    Defines the minimal API required by scripts that build and persist vector indexes.
    """

    def add_embeddings(self, texts: list[str], embs: list[list[float]], metadatas: list[dict]):
        """
        Purpose:
        Add a batch of texts with their embeddings and metadata to the store.

        Inputs:
        - texts: Original texts corresponding to embeddings.
        - embs: Embedding vectors.
        - metadatas: Per-text metadata dicts.

        Outputs:
        - None.

        Side effects:
        - Mutates the underlying store implementation.

        Failure modes:
        - Implementations may raise on inconsistent shapes or invalid data.
        """

        raise NotImplementedError

    def save(self, path: str):
        """
        Purpose:
        Persist the store contents to disk.

        Inputs:
        - path: Directory path to write into.

        Outputs:
        - None.

        Side effects:
        - Writes one or more files to disk.

        Failure modes:
        - Implementations may raise on empty stores or filesystem errors.
        """

        raise NotImplementedError

    @property
    def dimension(self) -> int | None:
        """
        Purpose:
        Report the embedding dimension when available.

        Inputs:
        - None.

        Outputs:
        - Dimension as an int, or `None` when not initialized.

        Side effects:
        - None.

        Failure modes:
        - None.
        """

        return None


class FaissStore(VectorStore):
    """
    Responsibility:
    Stores vectors in a FAISS `IndexIDMap(IndexFlatIP)` and stores chunk metadata/text in an accompanying SQLite file.

    Invariants:
    - Uses inner product similarity via `IndexFlatIP`.
    - Maintains a monotonically increasing integer ID for each added vector.
    """

    def __init__(self):
        """
        Purpose:
        Initialize an empty FAISS-backed store.

        Inputs:
        - None.

        Outputs:
        - None.

        Side effects:
        - Allocates internal record buffers; does not allocate a FAISS index until embeddings are added.

        Failure modes:
        - None.
        """

        self._index = None
        self._next_id = 0
        self._records: list[tuple[int, str, dict]] = []

    def add_embeddings(self, texts: list[str], embs: list[list[float]], metadatas: list[dict]):
        """
        Purpose:
        Add a batch of embeddings to the FAISS index and stage their corresponding records for SQLite persistence.

        Inputs:
        - texts: Text payloads to store alongside metadata.
        - embs: Embedding vectors (converted to `float32`).
        - metadatas: Per-text metadata dicts; each dict is copied before storage.

        Outputs:
        - None.

        Side effects:
        - Initializes the underlying FAISS index on the first call using the embedding dimensionality.
        - Adds vectors to the FAISS index with assigned integer IDs.
        - Appends to `self._records` for later SQLite write.

        Failure modes:
        - May raise if embeddings have inconsistent shape or cannot be converted to a NumPy array.
        """

        if not texts:
            return
        embs_np = np.asarray(embs, dtype=np.float32)
        if self._index is None:
            dim = int(embs_np.shape[1])
            base = faiss.IndexFlatIP(dim)
            self._index = faiss.IndexIDMap(base)
        ids = np.arange(self._next_id, self._next_id + len(embs_np), dtype=np.int64)
        self._next_id += len(embs_np)
        self._index.add_with_ids(embs_np, ids)
        for idx, text, meta in zip(ids, texts, metadatas):
            self._records.append((int(idx), text, meta.copy()))

    def save(self, path: str):
        """
        Purpose:
        Persist the FAISS vectors and SQLite metadata store to a directory.

        Inputs:
        - path: Target directory path.

        Outputs:
        - None.

        Side effects:
        - Writes `vectors.faiss` and `metadata.sqlite` into the target directory.
        - Creates the target directory if needed.

        Failure modes:
        - Raises `RuntimeError` when called before any data has been added.
        - May raise filesystem/SQLite exceptions during persistence.
        """

        if self._index is None or self._index.ntotal == 0:
            raise RuntimeError("No data added to vector store before save().")
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)

        faiss.write_index(self._index, str(target / "vectors.faiss"))

        conn = sqlite3.connect(target / "metadata.sqlite")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    vector_id INTEGER PRIMARY KEY,
                    email_id TEXT,
                    subject TEXT,
                    chunk_text TEXT,
                    metadata_json TEXT
                )
                """
            )
            rows = [
                (
                    rid,
                    record_meta.get("email_id"),
                    record_meta.get("subject"),
                    record_text,
                    json.dumps(record_meta, ensure_ascii=False),
                )
                for rid, record_text, record_meta in self._records
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    @property
    def dimension(self) -> int | None:
        """
        Purpose:
        Return the FAISS index dimensionality when initialized.

        Inputs:
        - None.

        Outputs:
        - Dimension as an int, or `None` if no embeddings have been added.

        Side effects:
        - None.

        Failure modes:
        - None.
        """

        if self._index is None:
            return None
        return int(self._index.d)


def archive_existing_index(base_dir: Path):
    """
    Purpose:
    Move existing files in an index directory into a timestamped archive folder.

    Inputs:
    - base_dir: Index directory path containing previously written index files.

    Outputs:
    - None.

    Side effects:
    - Renames files into `base_dir/archive/index_vYYYY-MM-DD_HHMM/`.
    - Prints a message when an archive is created.

    Failure modes:
    - May raise on filesystem errors (permissions, missing files, rename failures).
    """

    base_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = base_dir / "archive" / f"index_v{time.strftime('%Y-%m-%d_%H%M')}"
    existing = [p for p in base_dir.glob("*") if p.is_file()]
    if not existing:
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    for f in existing:
        f.rename(archive_dir / f.name)
    print(f"ℹ️ Archived old index to {archive_dir}")


def load_faiss_index_and_metadata(index_dir: Path) -> tuple[faiss.Index, dict[int, Chunk]]:
    """
    Purpose:
    Load a FAISS index and its corresponding chunk metadata from disk.

    Inputs:
    - index_dir: Directory containing `vectors.faiss` and `metadata.sqlite`.

    Outputs:
    - `(index, docs_by_id)` where `docs_by_id` maps vector ID to `Chunk`.

    Side effects:
    - Opens and closes an SQLite connection.

    Failure modes:
    - Raises `FileNotFoundError` if expected files do not exist.
    - May raise on FAISS read errors or SQLite query failures.
    - Silently drops invalid JSON metadata by substituting `{}` for that row.
    """

    index_path = index_dir / "vectors.faiss"
    metadata_path = index_dir / "metadata.sqlite"
    if not index_path.exists():
        raise FileNotFoundError(f"FAISS index not found: {index_path}")
    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata store not found: {metadata_path}")

    index = faiss.read_index(str(index_path))
    conn = sqlite3.connect(metadata_path)
    try:
        rows = conn.execute(
            "SELECT vector_id, email_id, subject, chunk_text, metadata_json FROM chunks"
        ).fetchall()
    finally:
        conn.close()

    docs: dict[int, Chunk] = {}
    for vector_id, email_id, subject, chunk_text, metadata_json in rows:
        try:
            metadata = json.loads(metadata_json) if metadata_json else {}
        except json.JSONDecodeError:
            metadata = {}
        if email_id and not metadata.get("email_id"):
            metadata["email_id"] = email_id
        if subject and not metadata.get("subject"):
            metadata["subject"] = subject
        docs[int(vector_id)] = Chunk(chunk_text, metadata)
    return index, docs
