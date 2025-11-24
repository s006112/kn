import os, json, time
import gc
import sqlite3
import torch
import faiss
import numpy as np
from transformers import AutoConfig, AutoModel
from math import ceil
from pathlib import Path

HF_EMBEDDING_MODEL = "jinaai/jina-embeddings-v3"
EMBEDDING_BATCH_SIZE = 16
CHUNK_BATCH_SIZE = 500

GPU_AVAILABLE = torch.cuda.is_available()
FAISS_GPU_AVAILABLE = hasattr(faiss, 'StandardGpuResources')
if GPU_AVAILABLE:
    torch.cuda.empty_cache()
    print(f"🚀 GPU detected: {torch.cuda.get_device_name(0)}")
    gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"🔧 GPU Memory: {gpu_mem:.1f} GB")

# Resolve key directories without relying on .env
PROJECT_ROOT = Path(__file__).resolve().parent
INDEX_DIR = (PROJECT_ROOT / "index").resolve()
CHUNKS_DIR = (PROJECT_ROOT / "data" / "clean").resolve()

if not CHUNKS_DIR.is_dir():
    raise FileNotFoundError(
        f"Chunks dir missing: {CHUNKS_DIR}. Run 01_chunk.py to generate chunk files first."
    )
if not os.access(CHUNKS_DIR, os.R_OK | os.X_OK):
    raise PermissionError(f"Chunks dir not readable: {CHUNKS_DIR}")

CHUNKS_JSONL_FILES = sorted(CHUNKS_DIR.glob("*.jsonl"))
print(f"ℹ️ Using chunks dir: {CHUNKS_DIR}")
print(f"ℹ️ Found {len(CHUNKS_JSONL_FILES)} JSONL files")

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

def safe_read_jsonl_line(line_bytes, line_num):
    fallback_encodings = ['utf-8', 'windows-1252', 'iso-8859-1', 'cp1252', 'latin1']
    for enc in fallback_encodings:
        try:
            text = line_bytes.decode(enc).strip()
            json.loads(text)
            return text, None
        except (UnicodeDecodeError, LookupError):
            continue
        except json.JSONDecodeError:
            return None, f"invalid JSON with {enc}"
    try:
        text = line_bytes.decode('utf-8', errors='replace').strip()
        return text, "decoded with replacement chars"
    except Exception:
        return None, "encoding failed completely"

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

# ── Common helpers ───────────────────────────────────────────────────────────
def l2_normalize(mat: list[list[float]]) -> list[list[float]]:
    arr = np.asarray(mat, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return arr.tolist()

# ── Swappable Vector Store Interface (minimal) ───────────────────────────────
class VectorStore:
    """Minimal interface to allow swapping vector backends."""

    def add_embeddings(self, texts: list[str], embs: list[list[float]], metadatas: list[dict]):
        raise NotImplementedError

    def save(self, path: str):
        raise NotImplementedError

    @property
    def dimension(self) -> int | None:
        return None

class FaissStore(VectorStore):
    """FAISS IDMap index paired with SQLite metadata storage."""

    def __init__(self):
        self._index = None
        self._next_id = 0
        self._records: list[tuple[int, str, dict]] = []

    def add_embeddings(self, texts: list[str], embs: list[list[float]], metadatas: list[dict]):
        if not texts:
            return
        embs_np = np.asarray(embs, dtype=np.float32)
        if self._index is None:
            dim = int(embs_np.shape[1])
            base = faiss.IndexFlatIP(dim)
            self._index = faiss.IndexIDMap(base)
        ids = np.arange(self._next_id, self._next_id + len(embs_np), dtype=np.int64)
        self._next_id += len(embs_np)
        self._index.add_with_ids(embs_np, ids)
        for idx, text, meta in zip(ids, texts, metadatas):
            # Store a shallow copy so downstream mutations do not affect persistence.
            self._records.append((int(idx), text, meta.copy()))

    def save(self, path: str):
        if self._index is None or self._index.ntotal == 0:
            raise RuntimeError("No data added to vector store before save().")
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)

        faiss.write_index(self._index, str(target / "vectors.faiss"))

        conn = sqlite3.connect(target / "metadata.sqlite")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    vector_id INTEGER PRIMARY KEY,
                    email_id TEXT,
                    subject TEXT,
                    chunk_text TEXT,
                    metadata_json TEXT
                )
                """
            )
            rows = [
                (
                    rid,
                    record_meta.get("email_id"),
                    record_meta.get("subject"),
                    record_text,
                    json.dumps(record_meta, ensure_ascii=False),
                )
                for rid, record_text, record_meta in self._records
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    @property
    def dimension(self) -> int | None:
        if self._index is None:
            return None
        return int(self._index.d)

class EmbeddingModel:
    """Lightweight wrapper around a Transformers model with encode()."""

    def __init__(self, model_name: str, device: str, batch_size: int, task: str):
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._task = task
        config = AutoConfig.from_pretrained(
            model_name,
            trust_remote_code=True,
        )
        if getattr(config, "use_flash_attn", False):
            print("ℹ️ Disabling flash attention for this model; using PyTorch attention instead")
            config.use_flash_attn = False

        self._model = AutoModel.from_pretrained(
            model_name,
            trust_remote_code=True,
            config=config,
        )
        if torch.cuda.is_available() and device.startswith("cuda"):
            self._model.to(device)
        self._model.eval()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with torch.no_grad():
            vectors = self._model.encode(
                texts,
                batch_size=self._batch_size,
                task=self._task,
                device=self._device,
            )
        if isinstance(vectors, np.ndarray):
            return vectors.tolist()
        if torch.is_tensor(vectors):
            return vectors.detach().cpu().numpy().tolist()
        return [list(vec) for vec in vectors]

def build_embeddings(device: str, batch_size: int) -> EmbeddingModel:
    return EmbeddingModel(
        model_name=HF_EMBEDDING_MODEL,
        device=device,
        batch_size=batch_size,
        task="retrieval.passage",
    )

def _archive_old_index(base_dir: Path):
    """Move existing files inside index/ into index/archive/index_vYYYY-MM-DD_HHMM/"""
    base_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = base_dir / "archive" / f"index_v{time.strftime('%Y-%m-%d_%H%M')}"
    # Only archive if there are files (avoid creating empty archives on first run)
    existing = [p for p in base_dir.glob("*") if p.is_file()]
    if not existing:
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    for f in existing:
        f.rename(archive_dir / f.name)
    print(f"ℹ️ Archived old index to {archive_dir}")

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
    embeddings = build_embeddings(device, EMBEDDING_BATCH_SIZE)

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
        cpu_emb = build_embeddings("cpu", 16)
        # CPU-only fallback, compute + normalize embeddings explicitly
        all_embs = cpu_emb.embed_documents(texts)
        all_embs = l2_normalize(all_embs)
        vector_store = FaissStore()
        vector_store.add_embeddings(texts, all_embs, metadatas)

    # ── SAVE INDEX ─────────────────────────────────────────────────────────────
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    # 1) Archive current index files (if any) to index/archive/index_vYYYY-MM-DD_HHMM/
    _archive_old_index(INDEX_DIR)

    # 2) Save NEW index files directly into index/
    #    (keep your original save call; only the folder is ensured above)
    vector_store.save(str(INDEX_DIR))
    print(f"✅ Index files written to {INDEX_DIR.resolve()}")

    print("✅ Index built and saved successfully")

if __name__ == "__main__":
    main()
