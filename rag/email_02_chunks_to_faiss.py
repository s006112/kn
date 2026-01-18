import gc
import json
import os
import time
from math import ceil

import faiss
import torch

from rag_embeddings import build_embeddings, l2_normalize
from rag_io_jsonl import safe_read_jsonl_line
from rag_vectorstore import FaissStore, archive_existing_index
from rag_config import OUTPUT_JSONL, INDEX_DIR

_BGE_M3_SNAPSHOT_PATH = "/root/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181"
HF_EMBEDDING_MODEL = (
    os.getenv("HF_EMBEDDING_MODEL")
    or (_BGE_M3_SNAPSHOT_PATH if os.path.isdir(_BGE_M3_SNAPSHOT_PATH) else "BAAI/bge-m3")
)
EMBEDDING_BATCH_SIZE = 16
CHUNK_BATCH_SIZE = 500

GPU_AVAILABLE = torch.cuda.is_available()
FAISS_GPU_AVAILABLE = hasattr(faiss, 'StandardGpuResources')
if GPU_AVAILABLE:
    torch.cuda.empty_cache()
    print(f"🚀 GPU detected: {torch.cuda.get_device_name(0)}")
    gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"🔧 GPU Memory: {gpu_mem:.1f} GB")

if not OUTPUT_JSONL.is_file():
    raise FileNotFoundError(
        f"Chunks file missing: {OUTPUT_JSONL}. Run 01_chunk.py to generate chunk files first."
    )
if not os.access(OUTPUT_JSONL, os.R_OK):
    raise PermissionError(f"Chunks file not readable: {OUTPUT_JSONL}")

CHUNKS_JSONL_FILES = [OUTPUT_JSONL]
print(f"ℹ️ Using chunks file: {OUTPUT_JSONL}")

def gpu_memory_cleanup():
    if GPU_AVAILABLE:
        try:
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        except Exception:
            pass
    gc.collect()
    time.sleep(0.1)

def setup_faiss_gpu():
    if not FAISS_GPU_AVAILABLE:
        return None
    try:
        res = faiss.StandardGpuResources()
        res.setDefaultNullStreamAllDevices()
        if hasattr(res, 'setTempMemory'):
            res.setTempMemory(int(2e9))
        print("✅ FAISS GPU resources initialized with memory limits")
        return res
    except Exception as e:
        print(f"⚠️ FAISS GPU setup failed: {e}, falling back to CPU")
        return None

def _resolve_email_id(rec: dict, metadata: dict) -> str | None:
    """Return the first non-empty identifier that lets us trace a chunk back to its email."""
    for candidate in (
        metadata.get("email_id"),
        rec.get("email_id"),
        rec.get("id"),
        metadata.get("thread_id"),
    ):
        if candidate:
            return candidate
    return None

def main():
    # ── LOAD CHUNKS ────────────────────────────────────────────────────────────
    if not CHUNKS_JSONL_FILES:
        print(f"❌ No .jsonl files found in {CHUNKS_DIR}")
        print("   Run 01_extract.py first to generate chunk files")
        return

    texts, metadatas = [], []
    skipped, encoding_issues, total_lines = 0, 0, 0

    for chunks_file in CHUNKS_JSONL_FILES:
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
                    email_id = _resolve_email_id(rec, metadata)
                    if not email_id:
                        print(
                            f"⚠️ Skipping {chunks_file.name}, line {ln}: missing email_id"
                        )
                        skipped += 1
                        continue
                    metadata["email_id"] = email_id

                    important_fields = ["subject", "from", "to", "date"]
                    metadata_lines = []
                    field_labels = {"subject": "Subject", "from": "From", "to": "To", "date": "Date"}
                    for field in important_fields:
                        value = metadata.get(field, "").strip()
                        if value:
                            metadata_lines.append(f"{field_labels[field]}: {value}")

                    # 移除 e5 "passage:" 前綴，因為 Jina v3 用 prompt_name 控制
                    combined_text = "\n".join(metadata_lines) + "\n" + rec["content"].strip()
                    texts.append(combined_text)
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
    if total_lines:
        drop_rate = 100 * skipped / total_lines
        print(f"   📉 Drop rate:           {drop_rate:.2f}%")
    print()

    if total == 0:
        print("❌ No usable chunks. Aborting index build.")
        return
    
    # ── INITIALIZE EMBEDDINGS ─────────────────────────────────────────────────
    device = 'cuda' if GPU_AVAILABLE else 'cpu'
    print(f"🔄 Initializing HuggingFace embeddings on {device.upper()}: {HF_EMBEDDING_MODEL}")
    embeddings = build_embeddings(
        model_name=HF_EMBEDDING_MODEL,
        device=device,
        batch_size=EMBEDDING_BATCH_SIZE,
        task="retrieval.passage",
    )

    # ── BUILD INDEX ────────────────────────────────────────────────────────────
    gpu_memory_cleanup()
    gpu_res = setup_faiss_gpu()
    print(f"🔄 Creating FAISS index on {'GPU' if gpu_res else 'CPU'}...")

    try:
        print("🔄 Generating embeddings in batches.")
        start_time = time.time()

        num_batches = ceil(total / CHUNK_BATCH_SIZE)
        backend = os.getenv("VECTOR_BACKEND", "faiss").lower()
        if backend != "faiss":
            raise NotImplementedError(f"Unsupported vector backend: {backend}")
        vector_store = FaissStore()

        for b in range(num_batches):
            start = b * CHUNK_BATCH_SIZE
            end   = min(start + CHUNK_BATCH_SIZE, total)
            print(f"  📦 Adding batch {b+1}/{num_batches} (texts {start+1}-{end})")

            # Compute embeddings explicitly and L2-normalize to unit vectors
            batch_texts = texts[start:end]
            batch_metas = metadatas[start:end]
            batch_embs = embeddings.embed_documents(batch_texts)
            batch_embs = l2_normalize(batch_embs)
            vector_store.add_embeddings(batch_texts, batch_embs, batch_metas)

            gpu_memory_cleanup()

        elapsed = time.time() - start_time
        print(f"✅ Embeddings generated in {elapsed:.2f} seconds")
        print("ℹ️ Keeping FAISS index on CPU to avoid transfer hanging issues")

    except Exception as e:
        print(f"❌ Error during vector store creation: {e}")
        print("🔄 Falling back to CPU-only processing…")
        cpu_emb = build_embeddings(
            model_name=HF_EMBEDDING_MODEL,
            device="cpu",
            batch_size=16,
            task="retrieval.passage",
        )
        # CPU-only fallback, compute + normalize embeddings explicitly
        all_embs = cpu_emb.embed_documents(texts)
        all_embs = l2_normalize(all_embs)
        vector_store = FaissStore()
        vector_store.add_embeddings(texts, all_embs, metadatas)

    # ── SAVE INDEX ─────────────────────────────────────────────────────────────
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    # 1) Archive current index files (if any) to index/archive/index_vYYYY-MM-DD_HHMM/
    archive_existing_index(INDEX_DIR)

    # 2) Save NEW index files directly into index/
    #    (keep your original save call; only the folder is ensured above)
    vector_store.save(str(INDEX_DIR))
    print(f"✅ Index files written to {INDEX_DIR.resolve()}")

    print("✅ Index built and saved successfully")

if __name__ == "__main__":
    main()
