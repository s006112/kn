import json
import sqlite3
import time
from pathlib import Path

import faiss
import numpy as np


class StdChunk:
    __slots__ = ("page_content", "metadata")

    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


class StandardFaissStore:
    """FAISS index + SQLite metadata for standards."""

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

    def save(self, path: Path):
        if self._index is None or self._index.ntotal == 0:
            raise RuntimeError("No data added to vector store before save().")
        path.mkdir(parents=True, exist_ok=True)

        faiss.write_index(self._index, str(path / "vectors.faiss"))

        conn = sqlite3.connect(path / "metadata.sqlite")
        try:
            conn.execute(
                """
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
                """
            )
            rows = [
                (
                    rid,
                    record_meta.get("doc_type"),
                    record_meta.get("doc_id"),
                    record_meta.get("doc_code"),
                    record_meta.get("location_path"),
                    record_meta.get("heading"),
                    record_text,
                    json.dumps(record_meta, ensure_ascii=False),
                )
                for rid, record_text, record_meta in self._records
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()


def archive_standard_index(base_dir: Path):
    """Archive existing index_std files, mirroring the email pipeline behavior."""
    base_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = base_dir / "archive" / f"index_v{time.strftime('%Y-%m-%d_%H%M')}"
    existing = [p for p in base_dir.glob("*") if p.is_file()]
    if not existing:
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    for f in existing:
        f.rename(archive_dir / f.name)
    print(f"ℹ️ Archived old standards index to {archive_dir}")


def load_standard_index(index_dir: Path) -> tuple[faiss.Index, dict[int, StdChunk]]:
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
            "SELECT vector_id, doc_type, doc_id, doc_code, location_path, heading, chunk_text, metadata_json FROM chunks"
        ).fetchall()
    finally:
        conn.close()

    docs: dict[int, StdChunk] = {}
    for vector_id, doc_type, doc_id, doc_code, location_path, heading, chunk_text, metadata_json in rows:
        try:
            metadata = json.loads(metadata_json) if metadata_json else {}
        except json.JSONDecodeError:
            metadata = {}
        if doc_type and not metadata.get("doc_type"):
            metadata["doc_type"] = doc_type
        if doc_id and not metadata.get("doc_id"):
            metadata["doc_id"] = doc_id
        if doc_code and not metadata.get("doc_code"):
            metadata["doc_code"] = doc_code
        if location_path and not metadata.get("location_path"):
            metadata["location_path"] = location_path
        if heading and not metadata.get("heading"):
            metadata["heading"] = heading
        docs[int(vector_id)] = StdChunk(chunk_text, metadata)
    return index, docs
