#!/usr/bin/env python3
"""Build FAISS index for standards JSONL."""

import json
import os
import sys
import time
from math import ceil
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent / "rag"
if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))

import faiss  # noqa: E402
import torch  # noqa: E402

from rag.rag_embeddings import build_embeddings, l2_normalize  # type: ignore  # noqa: E402
from rag.rag_io_jsonl import safe_read_jsonl_line  # type: ignore  # noqa: E402
from rag.rag_vectorstore import archive_existing_index  # type: ignore  # noqa: E402
from std_vectorstore import StandardFaissStore  # noqa: E402


HF_EMBEDDING_MODEL = "jinaai/jina-embeddings-v3"
EMBEDDING_BATCH_SIZE = 16  # smaller to reduce GPU memory pressure
CHUNK_BATCH_SIZE = 1000

PROJECT_ROOT = Path(__file__).resolve().parent
CHUNKS_DIR = (PROJECT_ROOT / "data" / "clean_std").resolve()
INDEX_DIR = (PROJECT_ROOT / "data" / "index_std").resolve()


def gpu_memory_cleanup():
    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        except Exception:
            pass


def _build_vector_store(texts, metadatas, device: str):
    print(f"🔄 Initializing embeddings on {device.upper()}: {HF_EMBEDDING_MODEL}")
    embeddings = build_embeddings(
        model_name=HF_EMBEDDING_MODEL,
        device=device,
        batch_size=EMBEDDING_BATCH_SIZE,
        task="retrieval.passage",
    )

    backend = os.getenv("VECTOR_BACKEND", "faiss").lower()
    if backend != "faiss":
        raise NotImplementedError(f"Unsupported vector backend: {backend}")
    vector_store = StandardFaissStore()

    print("🔄 Generating embeddings in batches.")
    num_batches = ceil(len(texts) / CHUNK_BATCH_SIZE)
    start_time = time.time()
    for b in range(num_batches):
        start = b * CHUNK_BATCH_SIZE
        end = min(start + CHUNK_BATCH_SIZE, len(texts))
        print(f"  📦 Adding batch {b+1}/{num_batches} (texts {start+1}-{end}) on {device}")

        batch_texts = texts[start:end]
        batch_metas = metadatas[start:end]
        batch_embs = embeddings.embed_documents(batch_texts)
        batch_embs = l2_normalize(batch_embs)
        vector_store.add_embeddings(batch_texts, batch_embs, batch_metas)

        gpu_memory_cleanup()

    elapsed = time.time() - start_time
    print(f"✅ Embeddings generated in {elapsed:.2f} seconds on {device}")
    return vector_store


def main():
    chunk_files = sorted(CHUNKS_DIR.glob("*.jsonl"))
    if not chunk_files:
        print(f"❌ No .jsonl files found in {CHUNKS_DIR}")
        print("   Run 01_std_chunk.py first to generate chunk files")
        return

    texts, metadatas = [], []
    skipped, encoding_issues, total_lines = 0, 0, 0

    for chunks_file in chunk_files:
        print(f"🔄 Loading chunks from {chunks_file.name}")
        with open(chunks_file, "rb") as f:
            for ln, raw in enumerate(f, start=1):
                total_lines += 1
                if not raw.strip():
                    skipped += 1
                    continue

                line, err = safe_read_jsonl_line(raw, ln)
                if line is None:
                    print(f"⚠️ Skipping {chunks_file.name}, line {ln}: {err}")
                    skipped += 1
                    encoding_issues += 1
                    continue
                if err:
                    print(f"⚠️ {chunks_file.name}, line {ln}: {err}")
                    encoding_issues += 1

                try:
                    rec = json.loads(line)
                    metadata = rec.get("metadata", {}).copy()
                    required = ("doc_id", "doc_code", "doc_type", "location_path")
                    if not all(metadata.get(k) for k in required):
                        print(f"⚠️ Skipping line {ln}: missing required fields in metadata {metadata}")
                        skipped += 1
                        continue
                    texts.append(rec["content"].strip())
                    metadatas.append(metadata)
                except (KeyError, json.JSONDecodeError) as e:
                    print(f"⚠️ Skipping {chunks_file.name}, line {ln}: {e}")
                    skipped += 1

    total = len(texts)
    print("📊 Summary:")
    print(f"   📦 Total lines read:   {total_lines}")
    print(f"   ✅ Loaded chunks:       {total}")
    print(f"   ⚠️ Skipped chunks:      {skipped}")
    print(f"   ⚠️ Encoding issues:     {encoding_issues}")
    print()

    if total == 0:
        print("❌ No usable chunks. Aborting index build.")
        return

    vector_store = None
    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        vector_store = _build_vector_store(texts, metadatas, device)
    except torch.cuda.OutOfMemoryError as exc:
        print(f"⚠️ CUDA OOM detected ({exc}); falling back to CPU")
        gpu_memory_cleanup()
        vector_store = _build_vector_store(texts, metadatas, "cpu")
    except Exception as exc:
        if device == "cuda":
            print(f"⚠️ GPU embedding failed ({exc}); retrying on CPU")
            gpu_memory_cleanup()
            vector_store = _build_vector_store(texts, metadatas, "cpu")
        else:
            raise

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    archive_existing_index(INDEX_DIR)
    vector_store.save(INDEX_DIR)
    print(f"✅ Standards index written to {INDEX_DIR.resolve()}")


if __name__ == "__main__":
    main()
