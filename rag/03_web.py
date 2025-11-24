#!/usr/bin/env python3
"""Gradio UI for querying email data via FAISS + Jina embeddings.
Designed for Hugging Face Spaces (sdk: gradio).
Phase 0: keep functionality identical, no Pydantic, no langchain_core.output_parsers.
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from collections import defaultdict

import gradio as gr
import numpy as np
import torch
import faiss
from openai import OpenAI
from dotenv import load_dotenv
from transformers import AutoConfig, AutoModel

# ─── Environment setup ──────────────────────────────────────────────────────
os.environ.update({"CUDA_VISIBLE_DEVICES": "", "TORCH_USE_CUDA_DSA": "0"})  # force CPU
sys.modules["torchvision"] = None  # avoid accidental heavy imports
load_dotenv()  # load API keys if present

# ─── Config ─────────────────────────────────────────────────────────────────
INDEX_DIR = Path(__file__).resolve().parent / "index"

if not INDEX_DIR.exists():
    raise FileNotFoundError(f"Index directory not found: {INDEX_DIR}")

# EMBED_MODEL = "intfloat/multilingual-e5-large-instruct"
EMBED_MODEL = "jinaai/jina-embeddings-v3"
EMBED_BATCH_SIZE = 16
LLM_MODEL = "gpt-4.1-mini"   # keep as-is; can be overridden by env if you like
TOP_K = 50
CHUNKS_PER_EMAIL = 20
MAX_TOKENS = 20000
MAX_EMAIL_HIT = 5
QUERY_PREFIX = ""  # Jina v3 不需 e5 的 "query: " 前綴
SCORE_THRESHOLD = 0.5


class Chunk:
    __slots__ = ("page_content", "metadata")

    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


class EmbeddingModel:
    """Minimal wrapper around a Transformers model's encode()."""

    def __init__(self, model_name: str, device: str, batch_size: int, task: str):
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

    def embed_query(self, text: str) -> np.ndarray:
        with torch.no_grad():
            vectors = self._model.encode(
                [text],
                batch_size=self._batch_size,
                task=self._task,
                device=self._device,
            )
        if isinstance(vectors, np.ndarray):
            vec = vectors[0]
        elif torch.is_tensor(vectors):
            vec = vectors[0].detach().cpu().numpy()
        else:
            vec = np.asarray(vectors[0], dtype=np.float32)
        vec = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec

FAISS_INDEX: faiss.Index | None = None
DOCS_BY_ID: dict[int, Chunk] = {}
GROUPED_DOCS: dict[str, list[Chunk]] = {}

def format_query(q: str) -> str:
    return f"{QUERY_PREFIX}{q.strip()}"

# ─── Persistence helpers ─────────────────────────────────────────────────────
def _load_index_and_metadata(index_dir: Path) -> tuple[faiss.Index, dict[int, Chunk]]:
    index_path = index_dir / "vectors.faiss"
    metadata_path = index_dir / "metadata.sqlite"
    if not index_path.exists():
        raise FileNotFoundError(f"FAISS index not found: {index_path}")
    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata store not found: {metadata_path}")

    index = faiss.read_index(str(index_path))
    conn = sqlite3.connect(metadata_path)
    try:
        rows = conn.execute(
            "SELECT vector_id, email_id, subject, chunk_text, metadata_json FROM chunks"
        ).fetchall()
    finally:
        conn.close()

    docs: dict[int, Chunk] = {}
    for vector_id, email_id, subject, chunk_text, metadata_json in rows:
        try:
            metadata = json.loads(metadata_json) if metadata_json else {}
        except json.JSONDecodeError:
            metadata = {}
        if email_id and not metadata.get("email_id"):
            metadata["email_id"] = email_id
        if subject and not metadata.get("subject"):
            metadata["subject"] = subject
        docs[int(vector_id)] = Chunk(chunk_text, metadata)
    return index, docs


def _build_grouped_docs(docs_by_id: dict[int, Chunk]) -> dict[str, list[Chunk]]:
    grouped: dict[str, list[Chunk]] = defaultdict(list)
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


def _faiss_search(vec: np.ndarray, k: int) -> list[tuple[Chunk, float, int]]:
    if FAISS_INDEX is None:
        return []
    query = np.asarray([vec], dtype=np.float32)
    distances, ids = FAISS_INDEX.search(query, k)
    hits: list[tuple[Chunk, float, int]] = []
    for idx, score in zip(ids[0], distances[0]):
        if idx == -1:
            continue
        doc = DOCS_BY_ID.get(int(idx))
        if not doc:
            continue
        hits.append((doc, float(score), int(idx)))
    return hits


def max_marginal_relevance_search(
    vec: np.ndarray, *, k: int, fetch_k: int, lambda_mult: float = 0.5
) -> list[Chunk]:
    candidates = _faiss_search(vec, fetch_k)
    if not candidates:
        return []

    candidate_vectors: dict[int, np.ndarray] = {}
    filtered = []
    for doc, score, idx in candidates:
        try:
            vec_i = FAISS_INDEX.reconstruct(int(idx))
        except RuntimeError:
            try:
                vec_i = FAISS_INDEX.index.reconstruct(int(idx))
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

    selected: list[tuple[Chunk, int]] = []
    selected_ids: set[int] = set()

    fetch_limit = min(k, len(filtered))
    for _ in range(fetch_limit):
        best_candidate: tuple[Chunk, float, int] | None = None
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


def similarity_search_with_score(vec: np.ndarray, k: int) -> list[tuple[Chunk, float]]:
    return [(doc, score) for doc, score, _ in _faiss_search(vec, k)]

# Initialize OpenAI client (preserve timeout behavior)
_OPENAI_TIMEOUT = 30
client = OpenAI(timeout=_OPENAI_TIMEOUT)

# ─── Load vector store and prepare grouping ─────────────────────────────────
print(f"🔄 Loading FAISS index from {INDEX_DIR}…")
FAISS_INDEX, DOCS_BY_ID = _load_index_and_metadata(INDEX_DIR)
GROUPED_DOCS = _build_grouped_docs(DOCS_BY_ID)

embedding_model = EmbeddingModel(
    model_name=EMBED_MODEL,
    device="cpu",
    batch_size=EMBED_BATCH_SIZE,
    task="retrieval.query",
)

print("✅ Index and retriever ready")

# ─── Prompt & QA chain (no JSON parser; plaintext/Markdown only) ────────────
prompt_path = Path(__file__).parent / "prompt_web.txt"
if prompt_path.exists():
    template_str = prompt_path.read_text("utf-8")
else:
    print("[WARN] prompt_web.txt Not exist, use default prompt")
    template_str = "Context:\n{context}\n\nQuestion:\n{question}"

# ─── Helpers ────────────────────────────────────────────────────────────────
def expand_chunks(docs):
    """Group by email and expand to multiple chunks per email; respect token budget."""
    seen, expanded, tokens = set(), [], 0
    warn_count = 0
    for d in docs:
        email_id = d.metadata.get("email_id")
        if not email_id:
            warn_count += 1
            if warn_count <= 5:
                print(f"[WARN] Missing email_id in chunk: {d.metadata}")
            continue
        if email_id in seen:
            continue
        seen.add(email_id)
        for chunk in GROUPED_DOCS.get(email_id, [])[:CHUNKS_PER_EMAIL]:
            est = len(chunk.page_content) // 4  # crude token estimate (kept)
            if tokens + est > MAX_TOKENS:
                return expanded, tokens
            expanded.append(chunk)
            tokens += est
    return expanded, tokens

# ─── Main QA path (no Pydantic; no JSON parsing) ───────────────────────────
def answer_question(raw_query: str):
    raw_query = (raw_query or "").strip()
    if not raw_query:
        return "", ""

    query = format_query(raw_query)
    start = time.time()

    # Manual retrieval (MMR diversification + scored pool) preserved
    q_vec = embedding_model.embed_query(query)

    mmr_docs = max_marginal_relevance_search(
        q_vec,
        k=TOP_K,
        fetch_k=50,
        lambda_mult=0.5,
    )
    scored_pool = similarity_search_with_score(q_vec, k=50)

    def _key(doc):
        email_id = doc.metadata.get("email_id")
        return (email_id, doc.metadata.get("seq"))

    score_map = { _key(doc): float(score) for doc, score in scored_pool }
    hits = [(doc, score_map.get(_key(doc), 0.0)) for doc in mmr_docs]
    hits.sort(key=lambda x: x[1], reverse=True)

    filtered_hits = [(doc, score) for doc, score in hits if score > SCORE_THRESHOLD]
    if not filtered_hits and hits:
        filtered_hits = [hits[0]]

    if MAX_EMAIL_HIT and MAX_EMAIL_HIT > 0:
        limited_hits: list[tuple[Chunk, float]] = []
        seen_limited: set[str] = set()
        for doc, score in filtered_hits:
            email_id = doc.metadata.get("email_id")
            if email_id:
                if email_id in seen_limited:
                    continue
                if len(seen_limited) >= MAX_EMAIL_HIT:
                    break
                seen_limited.add(email_id)
            limited_hits.append((doc, score))
        if limited_hits:
            filtered_hits = limited_hits

    raw_docs = [doc for doc, _ in filtered_hits]

    # Expand to multiple chunks per email (unchanged)
    docs, tokens = expand_chunks(raw_docs)
    expanded_counts: dict[str, int] = defaultdict(int)
    for chunk in docs:
        email_id = chunk.metadata.get("email_id")
        if email_id:
            expanded_counts[email_id] += 1

    # Build context and call OpenAI SDK directly (preserve prompt + temperature)
    try:
        context = "\n\n".join(getattr(d, "page_content", "") for d in docs)
        rendered = template_str.format(context=context, question=query)

        # Prefer Responses API (supports GPT-4.1 models); fallback to Chat Completions
        try:
            resp = client.responses.create(
                model=LLM_MODEL,
                input=rendered,
                temperature=0.0,
            )
            result_text = getattr(resp, "output_text", None)
            if not result_text:
                result_text = str(resp)
        except Exception:
            chat = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": rendered}],
                temperature=0.0,
            )
            result_text = (chat.choices[0].message.content or "").strip()

        result_text = (result_text or "").strip()
    except Exception as e:
        result_text = f"[ERROR] 回答失敗：{e}"

    # Build similarity table (unchanged)
    lines = ["| score | subject | chunk | date |", "|---:|---|---:|---|"]
    seen_email_ids: set[str] = set()
    for doc, sim in hits:
        raw_subject = str(doc.metadata.get("subject", "(no subject)"))
        if len(raw_subject) > 60:
            raw_subject = raw_subject[:60] + "..."
        subject = raw_subject.replace("|", "\\|")
        email_id = doc.metadata.get("email_id")
        if email_id and email_id in seen_email_ids:
            continue
        if email_id:
            seen_email_ids.add(email_id)
        expanded_size = expanded_counts.get(email_id, 0) if email_id else 0
        date = str(doc.metadata.get("date", "(no date)")).replace("|", "\\|")
        lines.append(f"| {sim:.4f} | {subject} | {expanded_size} | {date} |")
    sources_md = "\n".join(lines)

    print(f"🧠 Response generated in {time.time() - start:.2f}s; docs={len(docs)}, approx_tokens={tokens}")
    return result_text, sources_md

# ─── Gradio app ─────────────────────────────────────────────────────────────
with gr.Blocks(title="Email RAG Q&A") as demo:
    query = gr.Textbox(label="Question", placeholder="e.g. UCES project summary")
    ask_btn = gr.Button("Ask", variant="primary")
    answer = gr.Markdown(label="Answer")
    sources = gr.Markdown(label="Similarity Ranking (Top K)")

    # Only mouse click trigger, no enter trigger
    ask_btn.click(lambda: gr.update(interactive=False), None, [ask_btn]) \
        .then(answer_question, [query], [answer, sources]) \
        .then(lambda: gr.update(interactive=True), None, [ask_btn])

# Local run helper
if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))
    demo.launch(server_name="0.0.0.0", server_port=port, show_error=True)
