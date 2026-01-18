#!/usr/bin/env python3
import os, json
import sqlite3
from glob import glob
from pathlib import Path
import faiss
import torch
from transformers import AutoTokenizer, AutoModel

# ============================================================
# Model: BGE-M3 (MIT license, commercial friendly)
# ============================================================
MODEL_PATH = "/root/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

print(f"Loading embedding model: {MODEL_PATH}")
tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
model = AutoModel.from_pretrained(MODEL_PATH, local_files_only=True).cuda()  # assume GPU exists


def embed(texts):
    """Return L2-normalized dense embeddings for a list of texts."""
    with torch.no_grad():
        tokens = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=2048,  # 足够覆盖大部分頁級內容
            return_tensors="pt",
        ).to(model.device)

        out = model(**tokens)
        emb = out.last_hidden_state[:, 0]  # CLS pooling
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        return emb.cpu().numpy()


# ============================================================
# Paths
# ============================================================
# 改成指向存放 *.page_blocks.jsonl 的目錄
JSON_DIR = Path("data/standard/json")
INDEX_DIR = Path("data/standard/index")
PAGE_BLOCK_SUFFIX = ".page_blocks.jsonl"

os.makedirs(INDEX_DIR, exist_ok=True)


# ============================================================
# SQLite helper
# ============================================================
def init_sqlite(path: str) -> sqlite3.Connection:
    # 每次重建，避免 vector_id 主键冲突
    if os.path.exists(path):
        os.remove(path)

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE chunks (
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
    conn.commit()
    return conn


# ============================================================
# Load page-level blocks
# ============================================================
def load_chunks():
    """
    從 *.page_blocks.jsonl 加載 chunk：
    每行預期結構 roughly 為：
    {
        "block_id": "...",
        "file_id": "...",
        "page": 104,
        "char": 1234,
        "word": 250,
        "text": "......"
    }
    """
    pattern = os.path.join(JSON_DIR, f"*{PAGE_BLOCK_SUFFIX}")
    files = glob(pattern)
    chunks = []

    print(f"Looking for page_blocks: {pattern}")
    print(f"Found {len(files)} JSONL files")

    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)

                text = obj.get("text", "").strip()
                if not text:
                    continue

                file_id = obj.get("file_id")
                page = obj.get("page", 0)
                block_id = obj.get("block_id")

                # 給 SQLite 那幾個固定欄位一個合理映射
                meta = {
                    "doc_type": "page_block",
                    "doc_id": file_id,                  # 可以理解成檔案 ID
                    "doc_code": file_id,                # 目前直接共用；以後可換成標準號，如 UL935
                    "location_path": f"page:{page}",    # 粗略定位
                    "heading": None,                    # 暫時沒有 heading
                    # 其餘信息原樣保留
                    "block_id": block_id,
                    "page": page,
                    "char": obj.get("char"),
                    "word": obj.get("word"),
                }

                chunks.append((text, meta))

    print(f"Loaded {len(chunks)} chunks")
    return chunks


# ============================================================
# Simple terminal progress bar (no tqdm)
# ============================================================
def progress_bar(done: int, total: int):
    width = 40
    ratio = done / total if total else 1.0
    filled = int(width * ratio)
    bar = "█" * filled + "-" * (width - filled)
    print(f"\r[{bar}] {done}/{total} ({ratio*100:5.1f}%)", end="", flush=True)


# ============================================================
# Build FAISS index
# ============================================================
def build_index(chunks):
    total = len(chunks)
    if total == 0:
        print("No chunks to index.")
        return

    # 推断向量维度
    dim = embed(["test"]).shape[1]
    index = faiss.IndexFlatIP(dim)

    sqlite_path = os.path.join(INDEX_DIR, "metadata.sqlite")
    conn = init_sqlite(sqlite_path)
    cur = conn.cursor()

    SAFE_BATCH = 32
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
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    vector_id,
                    meta.get("doc_type"),
                    meta.get("doc_id"),
                    meta.get("doc_code"),
                    meta.get("location_path"),
                    meta.get("heading"),
                    texts[j],
                    meta_json,
                ),
            )
            vector_id += 1

        conn.commit()
        done = min(start_idx + SAFE_BATCH, total)
        progress_bar(done, total)

    print()  # 换行
    faiss.write_index(index, os.path.join(INDEX_DIR, "faiss.index"))
    print("FAISS index + metadata saved.")
    conn.close()


# ============================================================
# Main
# ============================================================
def main():
    chunks = load_chunks()
    build_index(chunks)


if __name__ == "__main__":
    main()
