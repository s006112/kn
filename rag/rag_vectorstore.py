import json
import sqlite3
import time
from pathlib import Path

import faiss
import numpy as np


class Chunk:
    __slots__ = ("page_content", "metadata")

    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


class VectorStore:
    """Minimal interface to allow swapping vector backends."""

    def add_embeddings(self, texts: list[str], embs: list[list[float]], metadatas: list[dict]):
        raise NotImplementedError

    def save(self, path: str):
        raise NotImplementedError

    @property
    def dimension(self) -> int | None:
        return None


class FaissStore(VectorStore):
    """FAISS IDMap index paired with SQLite metadata storage."""

    def __init__(self):
        self._index = None
        self._next_id = 0
        self._records: list[tuple[int, str, dict]] = []

    def add_embeddings(self, texts: list[str], embs: list[list[float]], metadatas: list[dict]):
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
        if self._index is None:
            return None
        return int(self._index.d)


def archive_existing_index(base_dir: Path):
    """Move existing files inside index/ into index/archive/index_vYYYY-MM-DD_HHMM/"""
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
