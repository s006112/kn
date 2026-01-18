#!/usr/bin/env python3
"""Gradio UI for querying email data via FAISS + Jina embeddings.
Designed for Hugging Face Spaces (sdk: gradio).
Phase 0: keep functionality identical, no Pydantic, no langchain_core.output_parsers.
"""

import os
import sys
import time
from pathlib import Path
from collections import defaultdict
from openai import OpenAI
from dotenv import load_dotenv
import gradio as gr

# LangChain pieces we still keep in Phase 0 (retrieval only)
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

# ─── Environment setup ──────────────────────────────────────────────────────
os.environ.update({"CUDA_VISIBLE_DEVICES": "", "TORCH_USE_CUDA_DSA": "0"})  # force CPU
sys.modules["torchvision"] = None  # avoid accidental heavy imports
load_dotenv()  # load API keys if present

# ─── Config ─────────────────────────────────────────────────────────────────
_idx_env = os.getenv("INDEX_DIR")
if _idx_env:
    INDEX_DIR = Path(_idx_env)
else:
    candidates = [Path("index"), Path("/root/email-rag/index")]
    INDEX_DIR = next((p for p in candidates if p.exists()), candidates[0])

# EMBED_MODEL = "intfloat/multilingual-e5-large-instruct"
EMBED_MODEL = "jinaai/jina-embeddings-v3"
LLM_MODEL = "gpt-4.1-mini"   # keep as-is; can be overridden by env if you like
TOP_K = 6
CHUNKS_PER_EMAIL = 5
MAX_TOKENS = 20000
QUERY_PREFIX = ""  # Jina v3 不需 e5 的 "query: " 前綴

def format_query(q: str) -> str:
    return f"{QUERY_PREFIX}{q.strip()}"

# Initialize OpenAI client (preserve timeout behavior)
_OPENAI_TIMEOUT = 30
client = OpenAI(timeout=_OPENAI_TIMEOUT)

# ─── Load vector store and prepare grouping ─────────────────────────────────
print(f"🔄 Loading FAISS index from {INDEX_DIR}…")
embeddings = HuggingFaceEmbeddings(
    model_name=EMBED_MODEL,
    model_kwargs={"device": "cpu", "trust_remote_code": True},
    encode_kwargs={
        "device": "cpu",
        "normalize_embeddings": True,      # unit vectors → IP ≈ cosine
        "prompt_name": "retrieval.query",  # must match indexing side's passage prompt
    },
)

# NOTE: allow_dangerous_deserialization=True kept to preserve existing behavior
vector_store = FAISS.load_local(
    str(INDEX_DIR), embeddings, allow_dangerous_deserialization=True
)

# Build email/thread grouping for expansion (unchanged logic)
from collections import defaultdict as _dd
GROUPED_DOCS = _dd(list)
for doc in vector_store.docstore._dict.values():  # TODO: later replace with sidecar
    msg_id = doc.metadata.get("message_id") or doc.metadata.get("email_id")
    if msg_id:
        GROUPED_DOCS[msg_id].append(doc)
    else:
        print(f"[WARN] Skipping chunk missing message_id/email_id: {doc.metadata}")
for docs in GROUPED_DOCS.values():
    docs.sort(key=lambda d: d.metadata.get("seq", 0))

print("✅ Index and retriever ready")

# ─── Prompt & QA chain (no JSON parser; plaintext/Markdown only) ────────────
prompt_path = Path(__file__).resolve().parents[2] / "prompt" / "prompt_email_web_gui.txt"
if prompt_path.exists():
    template_str = prompt_path.read_text("utf-8")
else:
    print("[WARN] prompt file not found, use default prompt")
    template_str = "Context:\n{context}\n\nQuestion:\n{question}"

# ─── Helpers ────────────────────────────────────────────────────────────────
def expand_chunks(docs):
    """Group by email and expand to multiple chunks per email; respect token budget."""
    seen, expanded, tokens = set(), [], 0
    warn_count = 0
    for d in docs:
        msg_id = d.metadata.get("message_id") or d.metadata.get("email_id")
        if not msg_id:
            warn_count += 1
            if warn_count <= 5:
                print(f"[WARN] Missing message_id/email_id in chunk: {d.metadata}")
            continue
        if msg_id in seen:
            continue
        seen.add(msg_id)
        for chunk in GROUPED_DOCS.get(msg_id, [])[:CHUNKS_PER_EMAIL]:
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
    import numpy as np
    q_vec = embeddings.embed_query(query)
    qn = np.linalg.norm(q_vec) or 1.0
    q_vec = (np.asarray(q_vec, dtype=np.float32) / qn).tolist()

    mmr_docs = vector_store.max_marginal_relevance_search_by_vector(
        q_vec, k=TOP_K, fetch_k=50, lambda_mult=0.5
    )
    scored_pool = vector_store.similarity_search_with_score_by_vector(q_vec, k=50)

    def _key(doc):
        mid = doc.metadata.get("message_id") or doc.metadata.get("email_id")
        return (mid, doc.metadata.get("seq"))

    score_map = { _key(doc): float(score) for doc, score in scored_pool }
    hits = [(doc, score_map.get(_key(doc), 0.0)) for doc in mmr_docs]
    hits.sort(key=lambda x: x[1], reverse=True)
    raw_docs = [doc for doc, _ in hits]

    # Expand to multiple chunks per email (unchanged)
    docs, tokens = expand_chunks(raw_docs)

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
    lines = ["| score | subject | date |", "|---:|---|---|"]
    for doc, sim in hits:
        subject = str(doc.metadata.get("subject", "(no subject)")).replace("|", "\\|")
        date = str(doc.metadata.get("date", "(no date)")).replace("|", "\\|")
        lines.append(f"| {sim:.4f} | {subject} | {date} |")
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
