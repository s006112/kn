"""
Responsibility:
Build and persist a FAISS vector index for email chunks stored in a JSONL file,
including basic metadata normalization (ensuring `email_id`) and archiving any
existing index directory before saving the new one.

Pipelines:
- chunks jsonl -> records -> texts -> embeddings -> faiss store -> index files

Invariants:
- The input chunks file is validated at import time; the module raises if
  `OUTPUT_JSONL` is missing or unreadable.
- Each indexed item stores a metadata dict that includes `email_id`.
- Embeddings are L2-normalized before being added to the FAISS-backed store.
- Index output is written under `INDEX_DIR` after archiving prior contents.

Out of scope:
- Extracting emails or generating the chunks JSONL file.
- Query-time retrieval, reranking, or serving APIs.
"""

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
    """
    Purpose:
    Best-effort GPU and CPU memory cleanup between embedding batches.

    Inputs:
    - None.

    Outputs:
    - None.

    Side effects:
    - Calls CUDA cache clearing and synchronization when available.
    - Triggers Python garbage collection and sleeps briefly.

    Failure modes:
    - Suppresses CUDA cleanup exceptions; other exceptions may propagate.
    """
    if GPU_AVAILABLE:
        try:
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        except Exception:
            pass
    gc.collect()
    time.sleep(0.1)

def setup_faiss_gpu():
    """
    Purpose:
    Initialize FAISS GPU resources when the FAISS GPU API is available.

    Inputs:
    - None.

    Outputs:
    - A `faiss.StandardGpuResources` instance, or `None` when unavailable or when
      initialization fails.

    Side effects:
    - Prints initialization status to stdout.

    Failure modes:
    - Returns `None` on initialization errors (CPU fallback is expected).
    """
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
    """
    Purpose:
    Resolve a stable identifier for an email chunk from record fields.

    Inputs:
    - rec: Parsed JSON object for a chunk record.
    - metadata: The record's `metadata` mapping (typically a shallow copy).

    Outputs:
    - The first non-empty candidate identifier, or `None` if no candidates exist.

    Side effects:
    - None.

    Failure modes:
    - None (pure lookup).
    """
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
    """
    Purpose:
    Load email chunks from JSONL, build embeddings, create a FAISS-backed store,
    and save index artifacts under `INDEX_DIR`.

    Inputs:
    - None.

    Outputs:
    - None.

    Side effects:
    - Reads `OUTPUT_JSONL`, prints progress, computes embeddings, archives prior
      index contents, and writes new index files.

    Failure modes:
    - Continues after per-line JSON/encoding issues by skipping invalid chunks.
    - Falls back to CPU-only embedding on exceptions during batched processing.
    """
    # ── LOAD CHUNKS ────────────────────────────────────────────────────────────
    if not CHUNKS_JSONL_FILES:
        print(f"❌ No .jsonl files found in {CHUNKS_JSONL_FILES}")
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
