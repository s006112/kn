#!/usr/bin/env python3
"""
Responsibility:
Inspect and print the first N stored vectors from a FAISS index file, including a
per-vector identifier and a truncated preview of vector values. Also inspect and
print the first N metadata rows from the accompanying SQLite store.

Used by:
* (no direct callers found)

Pipelines:
- read_index -> resolve_ids -> extract_vectors -> format_output -> stdout
- read_metadata -> format_output -> stdout

Invariants:
- Never mutates the index file.
- If the index is not an IndexIDMap, ids are sequential (0..ntotal-1).
- Vector extraction requires an index that exposes `get_xb()`.

Out of scope:
- Building or writing FAISS indexes.
- Performing similarity search.
- Supporting index types that do not expose stored vectors.
"""

import argparse
import sqlite3
from pathlib import Path

import faiss
import numpy as np

META_TYPE = "mbox"  # standard or mbox 

INDEX_PATH = Path("data/faiss") / f"{META_TYPE}_faiss.index"
METADATA_PATH = Path("data/faiss") / f"{META_TYPE}_metadata.sqlite"

#DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[1] / "data/faiss/std_faiss.index"
#DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[1] / "data/faiss/mbox_faiss.index"
#DEFAULT_METADATA_PATH = Path(__file__).resolve().parents[1] / "data/faiss/mbox_metadata.sqlite"

DEFAULT_NUM = 10
DEFAULT_DIMS = 10
DEFAULT_CHUNK_TEXT_LIMIT = 500


def load_index(index_path: Path):
    """
    Purpose:
    Load a FAISS index and return per-vector ids and a dense vector matrix.

    Inputs:
    - index_path: Path to a FAISS index readable by `faiss.read_index`.

    Outputs:
    - ids: 1D numpy array of int64 ids; from `id_map` when present, otherwise
      sequential (0..ntotal-1).
    - vectors: 2D numpy array of float32 vectors with shape (ntotal, d).

    Side effects:
    - Reads the index file from disk.

    Failure modes:
    - RuntimeError if the index does not expose stored vectors via `get_xb()`.
    - Exceptions raised by FAISS if the file cannot be read or is invalid.
    """
    index = faiss.read_index(str(index_path))

    if hasattr(index, "id_map"):
        ids = faiss.vector_to_array(index.id_map)
        base_index = faiss.downcast_index(index.index)
    else:
        ids = np.arange(index.ntotal, dtype=np.int64)
        base_index = faiss.downcast_index(index)

    if not hasattr(base_index, "get_xb"):
        raise RuntimeError(
            f"Index type {type(index).__name__} does not expose stored vectors"
        )

    xb_ptr = base_index.get_xb()
    buffer = faiss.rev_swig_ptr(xb_ptr, index.ntotal * index.d)
    vectors = np.array(buffer, dtype=np.float32).reshape(index.ntotal, index.d)

    return ids, vectors


def load_metadata_rows(metadata_path: Path, limit: int) -> list[sqlite3.Row]:
    """
    Purpose:
    Load up to `limit` rows from the SQLite metadata store.

    Inputs:
    - metadata_path: Path to metadata.sqlite.
    - limit: Max number of rows to return.

    Outputs:
    - List of sqlite3.Row objects from the `chunks` table.

    Side effects:
    - Opens and closes an SQLite connection.

    Failure modes:
    - Raises sqlite3.Error on connection or query failures.
    """
    conn = sqlite3.connect(metadata_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM chunks ORDER BY vector_id LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def main() -> None:
    """
    Purpose:
    Parse CLI args and print the first N vectors from the index.

    Inputs:
    - CLI args: `--index-path`, `--num`, `--dims`.

    Outputs:
    - Writes formatted vector previews to stdout.

    Side effects:
    - Reads an index file and metadata store from disk.

    Failure modes:
    - Exits with SystemExit if the index file path does not exist.
    - Propagates errors from `load_index`.
    """
    parser = argparse.ArgumentParser(
        description="Display the first N entries from a FAISS index."
    )
    parser.add_argument(
        "-n",
        "--num",
        type=int,
        default=DEFAULT_NUM,
        help="Number of vectors to display (default: 5)",
    )
    parser.add_argument(
        "--index-path",
        type=Path,
        default=INDEX_PATH,
        help="Path to the vectors.faiss file (default: data/index/vectors.faiss from repo root)",
    )
    parser.add_argument(
        "--dims",
        type=int,
        default=DEFAULT_DIMS,
        help="How many dimensions to show per vector (default: 8; use 0 to show all)",
    )
    parser.add_argument(
        "--chunk-text-limit",
        type=int,
        default=DEFAULT_CHUNK_TEXT_LIMIT,
        help="Maximum characters to show for chunk_text (default: 500; use 0 to show all)",
    )

    args = parser.parse_args()

    if not args.index_path.exists():
        raise SystemExit(f"Index file not found: {args.index_path}")

    ids, vectors = load_index(args.index_path)

    count = min(args.num, len(ids))
    if count == 0:
        print("Index is empty; nothing to show.")
        return

    metadata_path = METADATA_PATH
    if not metadata_path.exists():
        print(f"Metadata store not found: {metadata_path}")
        return

    metadata_rows = load_metadata_rows(metadata_path, count)
    if not metadata_rows:
        print("Metadata store is empty; nothing to show.")
        return

    print("\nMetadata preview:")
    for offset, row in enumerate(metadata_rows):
        row_dict = dict(row)
        if 'chunk_text' in row_dict and args.chunk_text_limit > 0:
            row_dict['chunk_text'] = row_dict['chunk_text'][:args.chunk_text_limit]
        print(f"[{offset}] {row_dict}")

    np.set_printoptions(suppress=True, linewidth=120)

    dims_to_show = None if args.dims == 0 else args.dims

    for offset in range(count):
        vector = vectors[offset]
        slice_len = vector.size if dims_to_show is None else min(vector.size, dims_to_show)
        snippet = vector[:slice_len]
        ellipsis = "" if slice_len == vector.size else " ..."

        print(f"[{offset}] vector_id={ids[offset]} dim={vector.size}")
        print(f"      values={snippet}{ellipsis}")


if __name__ == "__main__":
    main()
