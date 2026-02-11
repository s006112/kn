# rag/helper_query_rewriting.py
from __future__ import annotations

import numpy as np


def rewrite_query_variants(
    question: str,
    *,
    max_variants: int = 3,
) -> list[str]:
    """
    Generate retrieval-oriented query variants.

    Contract:
    - Always includes the original question.
    - Variants are anchor-heavy and short.
    - Safe fallback: returns [question] if anything goes wrong.
    """
    q = (question or "").strip()
    if not q:
        return [""]

    base = " ".join(q.split())
    q_upper = base.upper()

    # Positive anchors (domain heuristics)
    if "PVTECH" in q_upper:
        pos = ["PVTECH", "catalog", "product series"]
    else:
        pos = ["vendor", "catalog", "product series"]

    # Negative anchors (mainly for future hybrid search / semantic steering)
    neg = [
        "PFAS", "SVHC", "quotation", "RFQ",
        "UVA", "UVB", "365nm", "Bridgelux", "Seoul",
    ]

    variants = [
        base,
        f"{base} {' '.join(pos)}",
        f"{' '.join(pos)} {base}",
        f"{base} -({' OR '.join(neg)})",
    ]

    out, seen = [], set()
    for s in variants:
        s = " ".join(s.split())
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_variants:
            break

    return out if out else [base]


def merge_candidates_maxscore(
    results: list[tuple[np.ndarray, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray]:
    """
    Merge multiple (idx, scores) results by taking max score per idx.
    """
    best: dict[int, float] = {}

    for idx, scores in results:
        for i, s in zip(idx, scores):
            ii = int(i)
            ss = float(s)
            if ii not in best or ss > best[ii]:
                best[ii] = ss

    if not best:
        return np.empty(0, dtype=int), np.empty(0, dtype=float)

    items = sorted(best.items(), key=lambda x: x[1], reverse=True)
    out_idx = np.asarray([i for i, _ in items], dtype=int)
    out_scores = np.asarray([s for _, s in items], dtype=float)
    return out_idx, out_scores
