#!/usr/bin/env python3
"""Compact Flask UI for querying email data via FAISS + E5 embeddings."""

import os
import sys
import time
from pathlib import Path

import markdown
from collections import defaultdict
from dotenv import load_dotenv
from flask import Flask, request, render_template_string
from langchain.chains import RetrievalQA
from langchain_huggingface import HuggingFaceEmbeddings
from langchain.prompts import PromptTemplate
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI

# ─── Environment setup ──────────────────────────────────────────────────────
os.environ.update({"CUDA_VISIBLE_DEVICES": "", "TORCH_USE_CUDA_DSA": "0"})  # force CPU
sys.modules["torchvision"] = None  # avoid accidental heavy imports
load_dotenv()  # load API keys if present

# ─── Config ─────────────────────────────────────────────────────────────────
INDEX_DIR = Path("/root/email-rag/index")      # pre-built FAISS index location
# EMBED_MODEL = "intfloat/multilingual-e5-large-instruct"
EMBED_MODEL = "jinaai/jina-embeddings-v3"
LLM_MODEL = "gpt-4.1-mini"
#LLM_MODEL = "sonar"
#LLM_MODEL = "sonar-pro"
TOP_K = 6
CHUNKS_PER_EMAIL = 5
MAX_TOKENS = 20000
#QUERY_PREFIX = "query: "
QUERY_PREFIX = ""  # Jina v3 不需 e5 的 "query: " 前綴


# ✅ MODIFIED: 封装 query 格式化
def format_query(q: str) -> str:
    return f"{QUERY_PREFIX}{q.strip()}"


# ─── Load vector store and retriever ────────────────────────────────────────
print(f"🔄 Loading FAISS index from {INDEX_DIR}…")
embeddings = HuggingFaceEmbeddings(
    model_name=EMBED_MODEL,
    model_kwargs={"device": "cpu", "trust_remote_code": True},
    encode_kwargs={
        "device": "cpu",
        "normalize_embeddings": True,
        "prompt_name": "retrieval.query"  # 與索引端的 retrieval.passage 配對
    },
)
vector_store = FAISS.load_local(
    str(INDEX_DIR), embeddings, allow_dangerous_deserialization=True
)
retriever = vector_store.as_retriever(
    search_type="similarity", search_kwargs={"k": TOP_K}
)

GROUPED_DOCS = defaultdict(list)
for doc in vector_store.docstore._dict.values():
    msg_id = doc.metadata.get("message_id")
    if msg_id:
        GROUPED_DOCS[msg_id].append(doc)
    else:
        print(f"[WARN] Skipping chunk missing message_id: {doc.metadata}")
for docs in GROUPED_DOCS.values():
    docs.sort(key=lambda d: d.metadata.get("seq", 0))

print("✅ Index and retriever ready")

# ─── Prompt and QA chain ───────────────────────────────────────────────────
# ✅ MODIFIED: 加载 prompt 时 fallback
prompt_path = Path(__file__).parent / "prompt_web.txt"
if prompt_path.exists():
    template_str = prompt_path.read_text("utf-8")
else:
    print("[WARN] prompt_web.txt 不存在，使用默认模板")
    template_str = "Context:\n{context}\n\nQuestion:\n{question}"
prompt = PromptTemplate(input_variables=["context", "question"], template=template_str)
qa = RetrievalQA.from_chain_type(
    llm=ChatOpenAI(model_name=LLM_MODEL, temperature=0.0),
#    llm=ChatOpenAI(model_name=LLM_MODEL, temperature=0.0, api_key=os.getenv("PERPLEXITY_API_KEY"), base_url="https://api.perplexity.ai"),
    chain_type="stuff",
    retriever=retriever,
    return_source_documents=True,
    chain_type_kwargs={"prompt": prompt},
)

# ─── Helper functions ──────────────────────────────────────────────────────
def strip_prefix(text: str) -> str:
    for p in ("passage: ", "query: "):
        if text.startswith(p):
            return text[len(p):].strip()
    return text.strip()

def expand_chunks(docs):
    seen, expanded, tokens = set(), [], 0
    warn_count = 0  # ✅ MODIFIED: 控制 warning 打印数量
    for d in docs:
        msg_id = d.metadata.get("message_id")
        if not msg_id:
            warn_count += 1
            if warn_count <= 5:
                print(f"[WARN] Missing message_id in chunk: {d.metadata}")
            continue
        if msg_id in seen:
            continue
        seen.add(msg_id)
        for chunk in GROUPED_DOCS.get(msg_id, [])[:CHUNKS_PER_EMAIL]:
            est = len(chunk.page_content) // 4
            if tokens + est > MAX_TOKENS:
                return expanded, tokens
            expanded.append(chunk)
            tokens += est
    return expanded, tokens


# ─── Flask app and template ────────────────────────────────────────────────
app = Flask(__name__)
HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Email RAG Q&A</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; }
    pre {
      white-space: pre-wrap;
      word-wrap: break-word;
      overflow-x: auto;
      background-color: #f8f9fa;
      padding: 1rem;
      border-radius: 0.375rem;
      font-size: 0.95rem;
    }
  </style>
</head>
<body class="container py-5">
  <main>
    <h1 class="mb-4">Email Q&A</h1>

    <form method="post" class="input-group mb-3">
      <input type="text" name="query" class="form-control" placeholder="Ask a question"
             value="{{ query|default('') }}" {{ "autofocus" if not query else "" }}>
      <button class="btn btn-primary" type="submit">Ask</button>
    </form>

    {% if answer %}
    <section class="card mb-4">
      <div class="card-body">
        <h5 class="card-title">Answer</h5>
        <div class="card-text">{{ answer|safe }}</div>
      </div>
    </section>
    {% endif %}

    {% if sources %}
    <section class="card">
      <div class="card-header">
        <h6 class="mb-0">Cosine Similarity Ranking</h6>
      </div>
      <ul class="list-group list-group-flush">
        {% for src in sources %}
        <li class="list-group-item">{{ src }}</li>
        {% endfor %}
      </ul>
    </section>
    {% endif %}
  </main>
</body>
</html>"""



# ─── Main route ────────────────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def index():
    raw_query = request.form.get("query", "").strip() if request.method == "POST" else ""
    answer_html, sources = "", []

    if raw_query:
        query = format_query(raw_query)
        start = time.time()

        raw_hits = vector_store.similarity_search_with_score(query, k=TOP_K)
        hits = [(doc, 1 - dist / 2) for doc, dist in raw_hits]
        hits.sort(key=lambda x: x[1], reverse=True)
        raw_docs = [doc for doc, _ in hits]

        print("🔍 [DEBUG] RAW TOP DOCUMENTS:")
        for i, (doc, sim) in enumerate(hits):
            print(f"  [{i}] sim={sim:.4f}, subject={doc.metadata.get('subject')}")
            snippet = doc.page_content[:100].replace(chr(10), ' ')
            print(f"       → {snippet}")

        docs, tokens = expand_chunks(raw_docs)
        token_sum = 0
        print("📦 [DEBUG] EXPANDED CHUNKS USED:")
        for i, d in enumerate(docs):
            md = d.metadata
            chunk_tokens = len(d.page_content) // 4
            token_sum += chunk_tokens
            snippet = strip_prefix(d.page_content or "").splitlines()[0][:100]
            print(f"  [{i}] email_id={md.get('email_id')}, "
                  f"att={md.get('attachment')}, subject={md.get('subject')}, seq={md.get('seq')}, "
                  f"tokens~{chunk_tokens}")
            print(f"       → {snippet}")

        print(f"\n⏳ Total tokens used: {token_sum} / {MAX_TOKENS}")
        print(f"🔍 Top docs retrieved: {len(raw_docs)} → Expanded chunks: {len(docs)} → Total est. tokens: {tokens}")

        # ✅ MODIFIED: 增加容错
        try:
            result = qa.combine_documents_chain.run(input_documents=docs, question=query)
        except Exception as e:
            result = f"[ERROR] 回答失败：{e}"

#        answer_html = f"<pre>{result}</pre>"  # ✅ 以 <pre> 包裹原始文本，网页上原样显示
        # ✅ 轉換 markdown → HTML
        answer_html = markdown.markdown(
            result,
            extensions=["extra", "codehilite", "sane_lists", "nl2br"]
        )

        sources = []
        for doc, sim in hits:
            subject = doc.metadata.get("subject", "(no subject)")
            date = doc.metadata.get("date", "(no date)")
            sources.append(f"{sim:.4f} · {subject} · {date}")

        print(f"🧠 Response generated in {time.time() - start:.2f}s\n")
        print("🧾 LLM raw output:\n" + result)

    return render_template_string(HTML_TEMPLATE, query=raw_query, answer=answer_html, sources=sources)


# ─── Entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
