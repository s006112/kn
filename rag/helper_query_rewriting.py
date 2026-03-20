"""
helper_query_rewriting.py
Responsibility
This helper module constructs deterministic query variants for retrieval and merges per-variant candidate lists by keeping the highest score for each retrieved index.

Used by:
* rag/helper_rag_pipeline.py

Pipelines:
- question -> normalize -> anchor_expand -> dedupe -> variants
- results -> compare -> sort -> arrays

Invariants:
- `rewrite_query_variants` returns at least one string.
- Empty or whitespace-only input returns `[""]`.
- Returned query variants preserve first-seen order and contain no duplicates.
- `merge_candidates_maxscore` keeps only the maximum score observed for each index.
- Merged scores are sorted in descending order before conversion to NumPy arrays.

Out of scope:
- Embedding queries or executing retrieval.
- Validating domain heuristics against external data.
- Applying score thresholds or top-k truncation.
- Preserving duplicate candidate rows across variant results.
"""
from __future__ import annotations

import numpy as np


def rewrite_query_variants(
    question: str,
    *,
    max_variants: int = 3,
) -> list[str]:
    """
    Purpose:
    Build a short ordered list of retrieval query variants from one question string.

    Inputs:
    - question: Source question text to normalize and expand.
    - max_variants: Maximum number of unique variants to return from the fixed candidate list.

    Outputs:
    - A list of unique query strings beginning with the normalized base question when non-empty.
    - `[""]` when `question` is empty or whitespace-only.
    """
    q = (question or "").strip()
    if not q:
        return [""]

    base = " ".join(q.split())
    q_upper = base.upper()

    # Domain anchors keep retrieval biased toward catalog-style matches.
    if "PVTECH" in q_upper:
        pos = ["PVTECH", "catalog", "product series"]
    else:
        pos = ["vendor", "catalog", "product series"]

    # Negative terms suppress recurrent off-target matches in lexical retrieval flows.
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
    Purpose:
    Merge multiple retrieval result sets by index and retain the highest score seen for each index.

    Inputs:
    - results: Sequence of `(idx, scores)` NumPy-array pairs from retrieval passes.

    Outputs:
    - A tuple `(out_idx, out_scores)` sorted by descending score.
    - Empty NumPy arrays when no indices are present across all result sets.
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
