#!/usr/bin/env python3
"""Gradio UI for querying email data via FAISS + embeddings.
Designed for Hugging Face Spaces (sdk: gradio).
Phase 0: keep functionality identical, no Pydantic, no langchain_core.output_parsers.
"""

import os
import sys
import time
from pathlib import Path
from collections import defaultdict

import faiss
import gradio as gr
from openai import OpenAI
from dotenv import load_dotenv
from rag_config import INDEX_DIR
from rag_embeddings import EmbeddingModel
from rag_retrieval import (
    build_grouped_docs,
    max_marginal_relevance_search,
    similarity_search_with_score,
)
from rag_vectorstore import Chunk, load_faiss_index_and_metadata

# ─── Environment setup ──────────────────────────────────────────────────────
os.environ.update({"CUDA_VISIBLE_DEVICES": "", "TORCH_USE_CUDA_DSA": "0"})  # force CPU
sys.modules["torchvision"] = None  # avoid accidental heavy imports
load_dotenv()  # load API keys if present

# ─── Config ─────────────────────────────────────────────────────────────────
if not INDEX_DIR.exists():
    raise FileNotFoundError(f"Index directory not found: {INDEX_DIR}")

_BGE_M3_SNAPSHOT_PATH = "/root/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181"
EMBED_MODEL = (
    os.getenv("HF_EMBEDDING_MODEL")
    or (_BGE_M3_SNAPSHOT_PATH if os.path.isdir(_BGE_M3_SNAPSHOT_PATH) else "BAAI/bge-m3")
)
EMBED_BATCH_SIZE = 16
LLM_MODEL = "gpt-4.1-mini"   # keep as-is; can be overridden by env if you like
TOP_K = 50
CHUNKS_PER_EMAIL = 20
MAX_TOKENS = 20000
MAX_EMAIL_HIT = 5
QUERY_PREFIX = ""  # 不需 e5 的 "query: " 前綴
SCORE_THRESHOLD = 0.5

FAISS_INDEX: faiss.Index | None = None
DOCS_BY_ID: dict[int, Chunk] = {}
GROUPED_DOCS: dict[str, list[Chunk]] = {}

def format_query(q: str) -> str:
    return f"{QUERY_PREFIX}{q.strip()}"

# Initialize OpenAI client (preserve timeout behavior)
_OPENAI_TIMEOUT = 30
client = OpenAI(timeout=_OPENAI_TIMEOUT)

# ─── Load vector store and prepare grouping ─────────────────────────────────
print(f"🔄 Loading FAISS index from {INDEX_DIR}…")
FAISS_INDEX, DOCS_BY_ID = load_faiss_index_and_metadata(INDEX_DIR)
GROUPED_DOCS = build_grouped_docs(DOCS_BY_ID)

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
        FAISS_INDEX,
        DOCS_BY_ID,
        q_vec,
        k=TOP_K,
        fetch_k=50,
        lambda_mult=0.5,
    )
    scored_pool = similarity_search_with_score(FAISS_INDEX, DOCS_BY_ID, q_vec, k=50)

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
