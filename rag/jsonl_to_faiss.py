#!/usr/bin/env python3
"""
Responsibility:
Build a FAISS vector index and a SQLite metadata table from page-level chunk JSONL
files under `data/{TARGET_CHUNK_FOLDER}/json`, using a locally cached HuggingFace BGE-M3 model.

Pipelines:
- jsonl -> chunks -> embeddings -> faiss index -> sqlite metadata -> index files

Invariants:
- Embeddings are L2-normalized and indexed with `faiss.IndexFlatIP`.
- The SQLite database file at `data/faiss/{TARGET_CHUNK_FOLDER}_metadata.sqlite` is rebuilt on
  each run (any existing file is removed).
- Empty lines and chunks with empty `text` are skipped when loading JSONL.

Out of scope:
- Producing or validating the `*.page_blocks.jsonl` inputs.
- Query-time retrieval, reranking, or serving APIs.
"""

import os, json, sys
import sqlite3
import re
from glob import glob
from pathlib import Path
import faiss

# Add project root to sys.path for helper imports
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_embedding import embed

TARGET_CHUNK_FOLDER = "mbox"  #  mbox or standard

JSON_DIR = Path(f"data/{TARGET_CHUNK_FOLDER}/jsonl")
FAISS_DIR = Path("data/faiss")
BLOCK_SUFFIX = "blocks.jsonl"

# ============================================================

SAFE_BATCH = 16
HARD_MIN_WORDS = 10          # Rule 1: word <= 2 -> drop
SOFT_SHORT_WORDS = 10        # Rule 2: word < 8 and low-information -> drop
MAX_SPLIT_WORDS = 500       # Rule 3: word > MAX_SPLIT_WORDS -> split (never drop long)

_EN_STOPWORDS = {
    "a","an","the","and","or","but",
    "i","me","my","myself","we","our","ours","ourselves",
    "you","your","yours","yourself","yourselves",
    "he","him","his","himself","she","her","hers","herself",
    "it","its","itself","they","them","their","theirs","themselves",
    "this","that","these","those",
    "is","am","are","was","were","be","been","being",
    "have","has","had","having","do","does","did","doing",
    "to","of","in","on","for","with","as","at","by","from","into","about","over","under",
    "not","no","yes","ok","okay",
    "so","too","very","just","only",
}

_LOW_INFO_REGEXES = [
    re.compile(r"^(?:hi|hello|hey)[!. ,]*$", re.I),
    re.compile(r"^(?:ok|okay|k|noted|received|got it|roger|ack)[!. ,]*$", re.I),
    re.compile(r"^(?:thanks|thank you|thx|tks)[!. ,]*$", re.I),
    re.compile(r"^(?:best regards|kind regards|regards|br|cheers|sincerely)[!. ,]*$", re.I),
    re.compile(r"^(?:sent from my .+)$", re.I),
    re.compile(r"^(?:--+)$"),
    # 中文常见“无信息”短句 / 签名
    re.compile(r"^(?:谢谢|多谢|感谢|謝謝|多謝|感謝)[!！。．、,， ]*$"),
    re.compile(r"^(?:收到|已收到|收悉|悉知|已阅|已讀|已讀取|已了解|了解|明白|好的|好|可以|没问题|沒問題|OK|Ok|ok)[!！。．、,， ]*$"),
    re.compile(r"^(?:请了解|請了解|请知悉|請知悉|请查收|請查收|烦请查收|煩請查收|敬请查收|敬請查收)[!！。．、,， ]*$"),
    re.compile(r"^(?:收到|已收到|收悉|悉知)[!！。．、,， ]*(?:谢谢|多谢|感谢|謝謝|多謝|感謝)?[!！。．、,， ]*$"),
    re.compile(r"^(?:请了解|請了解|请知悉|請知悉|请查收|請查收|烦请查收|煩請查收|敬请查收|敬請查收)[!！。．、,， ]*(?:谢谢|多谢|感谢|謝謝|多謝|感謝)?[!！。．、,， ]*$"),
    re.compile(r"^(?:此致|敬礼|敬禮|祝好|順祝商祺|顺祝商祺|致礼|致禮|敬上)[!！。．、,， ]*$"),
    re.compile(r"^(?:发自我的iPhone|發自我的iPhone|发自我的手机|發自我的手機).*$"),
]


def _safe_int(v):
    try:
        return int(v)
    except Exception:
        return None


def _count_words(text: str) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    return len(t.split())


def _is_low_information(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True

    normalized = re.sub(r"\s+", " ", t).strip()
    for rx in _LOW_INFO_REGEXES:
        if rx.match(normalized):
            return True

    lower = normalized.lower()
    tokens = re.findall(r"[a-z]+(?:'[a-z]+)?", lower)
    if tokens and all(tok in _EN_STOPWORDS for tok in tokens):
        return True

    if not re.search(r"[a-z]", lower):
        if re.fullmatch(r"[\d\W_ ]+", lower):
            return True

    return False


def _split_long_text(text: str, *, max_words: int, word_count_hint: int | None = None) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if word_count_hint is not None:
        if word_count_hint <= max_words:
            return [t]
    elif _count_words(t) <= max_words:
        return [t]

    def pack_units(units: list[str]) -> list[str]:
        out: list[str] = []
        cur: list[str] = []
        cur_words = 0
        for u in units:
            u = u.strip()
            if not u:
                continue
            w = _count_words(u)
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


def init_sqlite(path: str) -> sqlite3.Connection:
    """
    Purpose:
    Create a new SQLite database containing a `chunks` table for vector metadata.

    Inputs:
    - path: Filesystem path for the SQLite database file.

    Outputs:
    - An open `sqlite3.Connection` with the schema created.

    Side effects:
    - Removes `path` if it already exists.
    - Creates and writes a SQLite file on disk.

    Failure modes:
    - Raises `OSError`/`sqlite3.Error` on filesystem or database errors.
    """
    # 每次重建，避免 vector_id 主键冲突
    if os.path.exists(path):
        os.remove(path)

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE chunks (
            vector_id INTEGER PRIMARY KEY,
            chunk_text TEXT,
            metadata_json TEXT
        )
    """
    )
    conn.commit()
    return conn


def load_chunks():
    """
    Purpose:
    Load page-level text blocks from `*.page_blocks.jsonl` files and map them to
    `(text, metadata)` tuples suitable for indexing and SQLite storage.

    Inputs:
    - None (reads files from `JSON_DIR` matching `BLOCK_SUFFIX`).

    Outputs:
    - List of `(text, meta)` tuples where `meta` is a dict containing fixed
      fields used by the SQLite schema plus original block identifiers.

    Side effects:
    - Reads JSONL files from disk.
    - Prints basic progress and counts to stdout.

    Failure modes:
    - Raises exceptions from file I/O or `json.loads` for malformed inputs.
    """
    pattern = os.path.join(JSON_DIR, f"*{BLOCK_SUFFIX}")
    files = glob(pattern)
    chunks = []
    stats = {
        "total_seen": 0,
        "drop_hard_word_le_2": 0,
        "drop_soft_short_low_info": 0,
        "split_long_blocks": 0,
        "split_long_subchunks_added": 0,
    }

    print(f"Looking for page_blocks: {pattern}")
    print(f"Found {len(files)} JSONL files")

    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                #if TARGET_CHUNK_FOLDER == "mbox":
                #    if obj.get("part") == "quote":
                #        continue

                text = (obj.get("text") or obj.get("content") or "").strip()
                if not text:
                    continue

                stats["total_seen"] += 1

                word_count = _safe_int(obj.get("word"))
                if word_count is None:
                    word_count = _count_words(text)
                char_count = _safe_int(obj.get("char"))
                if char_count is None:
                    char_count = len(text)

                if TARGET_CHUNK_FOLDER == "standard":
                    meta = {
                        # ── general schema fields ──
                        "doc_type": obj.get("file_type"),
                        "doc_id": obj.get("doc_id"),  # 可以理解成檔案 ID
                        "chunk_id": obj.get("block_id"),
                        "page": obj.get("page"),
                        "char": char_count,
                        "word": word_count,

                        # ── standard native fields ──
                        "source": obj.get("source"),
                    }                    

                elif TARGET_CHUNK_FOLDER == "mbox":
                    meta = {
                        # ── general schema fields ──
                        "doc_type": obj.get("file_type"),
                        "doc_id": obj.get("email_id"), 
                        "chunk_id": obj.get("block_id"),
                        "page": obj.get("page"),
                        "char": char_count,
                        "word": word_count,

                        # ── email native fields ──
                        "subject": obj.get("subject"),          # 人类可读标题（显示用）
                        "thread_id": obj.get("thread_id"),
                        "from": obj.get("from"),
                        "to": obj.get("to"),
                        "date": obj.get("date"),
                        #"part": obj.get("part"),  # redundant
                        #"attachment_type": obj.get("file_type"),    # duplicate
                        #"attachment_name": obj.get("attachment"),   # redundant
                    }

                # Rule 1: 极短块硬过滤
                if word_count <= (HARD_MIN_WORDS - 1):
                    stats["drop_hard_word_le_2"] += 1
                    continue

                # Rule 2: 短块 soft 过滤（短 + 低信息）
                if word_count < SOFT_SHORT_WORDS and _is_low_information(text):
                    stats["drop_soft_short_low_info"] += 1
                    continue

                # Rule 3: 长块拆分（不删除）
                if word_count > MAX_SPLIT_WORDS:
                    subs = _split_long_text(text, max_words=MAX_SPLIT_WORDS, word_count_hint=word_count)
                    if not subs:
                        continue
                    stats["split_long_blocks"] += 1
                    parts_total = len(subs)
                    for i, sub in enumerate(subs):
                        sub_word = _count_words(sub)
                        if sub_word <= (HARD_MIN_WORDS - 1):
                            stats["drop_hard_word_le_2"] += 1
                            continue
                        if sub_word < SOFT_SHORT_WORDS and _is_low_information(sub):
                            stats["drop_soft_short_low_info"] += 1
                            continue
                        meta2 = dict(meta)
                        meta2["char"] = len(sub)
                        meta2["word"] = sub_word
                        meta2["split_parent_chunk_id"] = meta.get("chunk_id")
                        meta2["split_index"] = i
                        meta2["split_total"] = parts_total
                        chunks.append((sub, meta2))
                        stats["split_long_subchunks_added"] += 1
                    continue

                chunks.append((text, meta))

    print(f"Loaded {len(chunks)} chunks")
    total_seen = stats["total_seen"] or 1
    print(
        "Filter stats: "
        f"total_seen={stats['total_seen']}, "
        f"drop_hard_word_le_2={stats['drop_hard_word_le_2']} ({stats['drop_hard_word_le_2']/total_seen:.1%}), "
        f"drop_soft_short_low_info={stats['drop_soft_short_low_info']} ({stats['drop_soft_short_low_info']/total_seen:.1%}), "
        f"split_long_blocks={stats['split_long_blocks']} ({stats['split_long_blocks']/total_seen:.1%}), "
        f"split_long_subchunks_added={stats['split_long_subchunks_added']}"
    )
    return chunks


def progress_bar(done: int, total: int):
    """
    Purpose:
    Print an in-place progress bar to stdout.

    Inputs:
    - done: Completed item count.
    - total: Total item count.

    Outputs:
    - None.

    Side effects:
    - Writes to stdout without a trailing newline.

    Failure modes:
    - None (best-effort display).
    """
    width = 100
    ratio = done / total if total else 1.0
    filled = int(width * ratio)
    bar = "█" * filled + "-" * (width - filled)
    print(f"\r[{bar}] {done}/{total} ({ratio*100:5.1f}%)", end="", flush=True)


def build_index(chunks):
    """
    Purpose:
    Build a FAISS inner-product index over chunk embeddings and persist both the
    FAISS index and per-vector metadata.

    Inputs:
    - chunks: List of `(text, meta)` tuples as returned by `load_chunks()`.

    Outputs:
    - None.

    Side effects:
    - Computes embeddings via `embed()`.
    - Writes `faiss.index` and `metadata.sqlite` under `FAISS_DIR`.
    - Prints progress to stdout.

    Failure modes:
    - Raises exceptions from embedding/model execution, FAISS, SQLite, or file I/O.
    """
    total = len(chunks)
    if total == 0:
        print("No chunks to index.")
        return

    # 推断向量维度
    dim = embed(["test"]).shape[1]
    index = faiss.IndexFlatIP(dim)

    sqlite_path = os.path.join(FAISS_DIR, f"{TARGET_CHUNK_FOLDER}_metadata.sqlite")
    conn = init_sqlite(sqlite_path)
    cur = conn.cursor()

    vector_id = 0

    for start_idx in range(0, total, SAFE_BATCH):
        batch = chunks[start_idx : start_idx + SAFE_BATCH]
        texts = [t for (t, _) in batch]
        metas = [m for (_, m) in batch]

        embs = embed(texts)
        index.add(embs)

        for j, meta in enumerate(metas):
            meta_json = json.dumps(meta, ensure_ascii=False)
            cur.execute(
                "INSERT INTO chunks VALUES (?, ?, ?)",
                (
                    vector_id,
                    texts[j],
                    meta_json,
                ),
            )
            vector_id += 1

        conn.commit()
        done = min(start_idx + SAFE_BATCH, total)
        progress_bar(done, total)

    print()  # 换行
    faiss.write_index(index, os.path.join(FAISS_DIR, f"{TARGET_CHUNK_FOLDER}_faiss.index"))
    print("FAISS index + metadata saved.")
    conn.close()

def main():
    """
    Purpose:
    Load chunks from disk and build the FAISS + SQLite outputs.

    Inputs:
    - None.

    Outputs:
    - None.

    Side effects:
    - Reads JSONL inputs, runs embedding inference, and writes index artifacts.

    Failure modes:
    - Propagates exceptions from `load_chunks()` and `build_index()`.
    """
    chunks = load_chunks()
    build_index(chunks)


if __name__ == "__main__":
    main()
