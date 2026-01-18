"""
Responsibility:
Implements lightweight retrieval helpers on top of a FAISS index and in-memory `Chunk` records (grouping by email and basic similarity/MMR selection).

Used by:
* rag/email_03_web_gui.py

Pipelines:
- group_chunks -> search_faiss -> mmr_select -> return_chunks

Invariants:
- Retrieval uses the FAISS index scores as returned by `index.search` (e.g., inner product distances for IP indexes).
- `max_marginal_relevance_search` reconstructs candidate vectors from the index and normalizes them before computing pairwise similarities.

Out of scope:
- Computing embeddings for queries/documents.
- Building or persisting FAISS indexes and metadata stores.
"""

from collections import defaultdict
from typing import Dict, List, Tuple

import faiss
import numpy as np

from rag_vectorstore import Chunk


def build_grouped_docs(docs_by_id: Dict[int, Chunk]) -> Dict[str, List[Chunk]]:
    """
    Purpose:
    Group `Chunk` records by `metadata["email_id"]` and sort each group into a stable display order.

    Inputs:
    - docs_by_id: Mapping from vector ID to `Chunk`.

    Outputs:
    - Mapping from `email_id` to a list of chunks for that email, sorted by sequence metadata.

    Side effects:
    - Prints a warning for chunks missing `email_id` metadata.

    Failure modes:
    - May raise if a chunk has `metadata` that is not dict-like (used during sorting).
    """

    grouped: Dict[str, List[Chunk]] = defaultdict(list)
    for doc in docs_by_id.values():
        meta = doc.metadata or {}
        email_id = meta.get("email_id")
        if email_id:
            grouped[email_id].append(doc)
        else:
            print(f"[WARN] Skipping chunk missing email_id: {meta}")
    for chunks in grouped.values():
        chunks.sort(
            key=lambda d: (
                d.metadata.get("seq", d.metadata.get("chunk", 0)),
                d.metadata.get("chunk", 0),
            )
        )
    return grouped


def faiss_search(index: faiss.Index, docs_by_id: Dict[int, Chunk], vec: np.ndarray, k: int) -> List[Tuple[Chunk, float, int]]:
    """
    Purpose:
    Perform a FAISS nearest-neighbor search and map hit IDs back to `Chunk` records.

    Inputs:
    - index: FAISS index supporting `search`.
    - docs_by_id: Mapping from FAISS vector IDs to `Chunk`.
    - vec: Query vector (1D).
    - k: Number of neighbors to request from FAISS.

    Outputs:
    - List of `(chunk, score, vector_id)` tuples for hits that resolve to existing chunks.

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions from FAISS `search`.
    """

    query = np.asarray([vec], dtype=np.float32)
    distances, ids = index.search(query, k)
    hits: List[Tuple[Chunk, float, int]] = []
    for idx, score in zip(ids[0], distances[0]):
        if idx == -1:
            continue
        doc = docs_by_id.get(int(idx))
        if not doc:
            continue
        hits.append((doc, float(score), int(idx)))
    return hits


def max_marginal_relevance_search(
    index: faiss.Index,
    docs_by_id: Dict[int, Chunk],
    vec: np.ndarray,
    *,
    k: int,
    fetch_k: int,
    lambda_mult: float = 0.5,
) -> List[Chunk]:
    """
    Purpose:
    Select up to `k` chunks using greedy maximal marginal relevance over FAISS candidates.

    Inputs:
    - index: FAISS index supporting `search` and vector reconstruction.
    - docs_by_id: Mapping from FAISS vector IDs to `Chunk`.
    - vec: Query vector (1D).
    - k: Number of chunks to return.
    - fetch_k: Number of initial candidates to fetch from FAISS before MMR filtering.
    - lambda_mult: Trade-off between relevance (score) and diversity (max similarity to selected).

    Outputs:
    - List of selected `Chunk` objects, in selection order.

    Side effects:
    - Reconstructs candidate vectors from the index to compute candidate-to-candidate similarities.

    Failure modes:
    - Returns fewer than `k` results when reconstruction fails or candidates are insufficient.
    - Propagates exceptions from FAISS `search`; reconstruction errors are handled by skipping candidates.
    """

    candidates = faiss_search(index, docs_by_id, vec, fetch_k)
    if not candidates:
        return []

    candidate_vectors: dict[int, np.ndarray] = {}
    filtered = []
    for doc, score, idx in candidates:
        try:
            vec_i = index.reconstruct(int(idx))
        except RuntimeError:
            try:
                vec_i = index.index.reconstruct(int(idx))
            except Exception:
                continue
        arr = np.asarray(vec_i, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr = arr / norm
        candidate_vectors[int(idx)] = arr
        filtered.append((doc, float(score), int(idx)))

    if not filtered:
        return []

    selected: List[Tuple[Chunk, int]] = []
    selected_ids: set[int] = set()

    fetch_limit = min(k, len(filtered))
    for _ in range(fetch_limit):
        best_candidate: Tuple[Chunk, float, int] | None = None
        best_score = -float("inf")
        for doc, score, idx in filtered:
            if idx in selected_ids:
                continue
            if not selected:
                mmr_score = score
            else:
                max_sim = max(
                    float(np.dot(candidate_vectors[idx], candidate_vectors[s_idx]))
                    for _, s_idx in selected
                )
                mmr_score = lambda_mult * score - (1.0 - lambda_mult) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_candidate = (doc, score, idx)
        if best_candidate is None:
            break
        doc, _, idx = best_candidate
        selected.append((doc, idx))
        selected_ids.add(idx)

    return [doc for doc, _ in selected]


def similarity_search_with_score(index: faiss.Index, docs_by_id: Dict[int, Chunk], vec: np.ndarray, k: int) -> List[Tuple[Chunk, float]]:
    """
    Purpose:
    Convenience wrapper around `faiss_search` that drops the returned vector IDs.

    Inputs:
    - index: FAISS index supporting `search`.
    - docs_by_id: Mapping from FAISS vector IDs to `Chunk`.
    - vec: Query vector (1D).
    - k: Number of neighbors to request from FAISS.

    Outputs:
    - List of `(chunk, score)` tuples.

    Side effects:
    - None.

    Failure modes:
    - Propagates exceptions from FAISS `search`.
    """

    return [(doc, score) for doc, score, _ in faiss_search(index, docs_by_id, vec, k)]
