from collections import defaultdict
from typing import Dict, List, Tuple

import faiss
import numpy as np

from rag_vectorstore import Chunk


def build_grouped_docs(docs_by_id: Dict[int, Chunk]) -> Dict[str, List[Chunk]]:
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
    return [(doc, score) for doc, score, _ in faiss_search(index, docs_by_id, vec, k)]
