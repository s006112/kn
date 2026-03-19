
"""
parse_block_to_chunk.py

Responsibility:
Read canonical-block JSONL files, filter low-value blocks, split oversized blocks, and write a chunked JSONL suitable for
downstream indexing.

Used by:
* rag/parse_mbox_to_chunk.py

Pipelines:
- jsonl_glob -> jsonl_read -> text_extract -> word_count -> short_drop -> low_info_drop -> long_split -> chunk_write

Invariants:
- Output is JSONL with one object per line.
- Each output line preserves all input object keys, with `text` set to the kept/split chunk value.
- Blocks with `text` missing/blank are skipped.
- Output file is overwritten on each run.

Out of scope:
- Parsing raw emails or attachments into canonical blocks.
- Embedding, vector indexing, or similarity search.
- Deduplication or thread reconstruction.
"""

import os
import json
import re
from glob import glob
from pathlib import Path
from parse_raw_to_jsonl import count_text_metrics


# =========================
# config
# =========================

HARD_MIN_WORDS = 10          # Drops extremely short blocks that are unlikely to be retrievable context.
MAX_SPLIT_WORDS = 500        # Splits long blocks to cap chunk size while keeping content.


def _split_long_text(text: str, *, max_words: int, word_count_hint: int | None = None) -> list[str]:
    """
    Purpose:
    Split a long text into subtexts that each stay under a max word budget, preferring paragraph boundaries, then sentence
    boundaries, then fixed-size word windows.

    Inputs:
    - text: Input text.
    - max_words: Maximum word count per returned subtext.
    - word_count_hint: Optional precomputed word count for `text`.

    Outputs:
    - List of non-empty subtexts, each with word count <= max_words (except when recursive splitting is required).
    """
    t = (text or "").strip()
    if not t:
        return []
    if word_count_hint is not None:
        wc = word_count_hint
    else:
        _, wc = count_text_metrics(t)

    if wc <= max_words:
        return [t]

    def pack_units(units: list[str]) -> list[str]:
        out: list[str] = []
        cur: list[str] = []
        cur_words = 0
        for u in units:
            u = u.strip()
            if not u:
                continue
            _, w = count_text_metrics(u)
            if w > max_words:
                if cur:
                    out.append(" ".join(cur).strip())
                    cur, cur_words = [], 0
                out.extend(_split_long_text(u, max_words=max_words, word_count_hint=w))
                continue
            if cur_words and (cur_words + w) > max_words:
                out.append(" ".join(cur).strip())
                cur, cur_words = [], 0
            cur.append(u)
            cur_words += w
        if cur:
            out.append(" ".join(cur).strip())
        return [x for x in out if x]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", t) if p.strip()]
    if len(paragraphs) > 1:
        return pack_units(paragraphs)

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", t)) if s.strip()]
    if len(sentences) > 1:
        return pack_units(sentences)

    words = t.split()
    if not words:
        return []
    out = []
    for i in range(0, len(words), max_words):
        out.append(" ".join(words[i : i + max_words]).strip())
    return [x for x in out if x]

# ==========================================

def _dump_chunks_jsonl(chunks, out_path: Path):
    """
    Purpose:
    Write (text, metadata) chunk pairs to a JSONL file, ensuring `text` is the final key written into each object.

    Inputs:
    - chunks: Iterable of (text, meta_dict) tuples.
    - out_path: Output file path.

    Outputs:
    - None. Overwrites `out_path`.
    """
    with open(out_path, "w", encoding="utf-8") as f:
        for text, meta in chunks:
            #obj = {"text": text, **meta}
            obj = {**meta, "text": text}
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _make_chunk(text: str, base_meta: dict, idx: int, total: int):
    char, word = count_text_metrics(text)

    meta2 = base_meta.copy()
    meta2["char"] = char
    meta2["word"] = word
    meta2["chunk_index"] = idx
    meta2["chunk_total"] = total

    return text, meta2

def build_chunks_jsonl(json_dir: Path, block_suffix: str, out_path: Path):
    pattern = os.path.join(json_dir, f"*{block_suffix}")
    files = glob(pattern)
    base_stem = out_path.stem
    if base_stem.endswith("_chunks"):
        base_stem = base_stem[:-7]

    chunks = []
    drop_logs = []
    split_logs = []

    stats = {
        "drop_hard": 0,
        "split_blocks": 0,
        "split_added": 0,
    }

    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)

                text = (obj.get("text") or "").strip()
                if not text:
                    continue

                _, word = count_text_metrics(text)
                meta = obj.copy()

                if word <= HARD_MIN_WORDS:
                    stats["drop_hard"] += 1
                    drop_logs.append({
                        **meta,
                        "reason": "hard_min_words",
                        "original_word": word,
                        "text": text,
                    })
                    continue

                if word > MAX_SPLIT_WORDS:
                    subs = _split_long_text(text, max_words=MAX_SPLIT_WORDS, word_count_hint=word)

                    stats["split_blocks"] += 1
                    total = len(subs)

                    split_logs.append({
                        **meta,
                        "type": "split_parent",
                        "reason": "split",
                        "original_word": word,
                        "split_total": total,
                        "text": text,
                    })

                    for i, sub in enumerate(subs, 1):
                        chunk_text, chunk_meta = _make_chunk(sub, meta, i, total)
                        chunks.append((chunk_text, chunk_meta))

                        split_logs.append({
                            **chunk_meta,
                            "type": "split_child",
                            "parent_block_id": meta.get("block_id"),
                            "text": chunk_text,
                        })

                    split_logs.append(None)
                    stats["split_added"] += total
                    continue

                chunks.append(_make_chunk(text, meta, 1, 1))

    _dump_chunks_jsonl(chunks, out_path)

    if drop_logs:
        drop_path = out_path.with_name(base_stem + "_drop.jsonl")
        _dump_chunks_jsonl([(x["text"], x) for x in drop_logs], drop_path)

    if split_logs:
        split_path = out_path.with_name(base_stem + "_split_added.jsonl")
        with open(split_path, "w", encoding="utf-8") as f:
            for x in split_logs:
                if x is None:
                    f.write("\n")
                else:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")

    print(f"CLEAN STATS: {stats}\nCHUNKS WRITTEN → {out_path}")

