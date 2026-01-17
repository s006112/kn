#!/usr/bin/env python3
"""Utility to inspect the first N vectors stored in a FAISS index."""

import argparse
from pathlib import Path

import faiss
import numpy as np

DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[1] / "data/index/faiss.index"


def load_index(index_path: Path):
    """Load a FAISS IndexIDMap and return ids array + dense vector matrix."""
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Display the first N entries from a FAISS index."
    )
    parser.add_argument(
        "-n",
        "--num",
        type=int,
        default=10,
        help="Number of vectors to display (default: 5)",
    )
    parser.add_argument(
        "--index-path",
        type=Path,
        default=DEFAULT_INDEX_PATH,
        help="Path to the vectors.faiss file (default: data/index/vectors.faiss from repo root)",
    )
    parser.add_argument(
        "--dims",
        type=int,
        default=20,
        help="How many dimensions to show per vector (default: 8; use 0 to show all)",
    )

    args = parser.parse_args()

    if not args.index_path.exists():
        raise SystemExit(f"Index file not found: {args.index_path}")

    ids, vectors = load_index(args.index_path)

    count = min(args.num, len(ids))
    if count == 0:
        print("Index is empty; nothing to show.")
        return

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
