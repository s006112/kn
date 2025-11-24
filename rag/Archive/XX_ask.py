#!/usr/bin/env python3
# scripts/03_ask.py

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Disable PyTorch/CUDA before any imports that might trigger the conflict
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['TORCH_USE_CUDA_DSA'] = '0'

# Monkey patch to avoid torchvision import issues
sys.modules['torchvision'] = None

# Use updated imports to avoid deprecation warnings
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.chat_models import ChatOpenAI
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate

# ─── CONFIG ────────────────────────────────────────────────────────────────
load_dotenv()

INDEX_DIR = Path("/root/email-rag/index")
HF_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
LLM_MODEL = "gpt-4.1-mini"
TOP_K = 8

# ─── LOAD VECTOR STORE ─────────────────────────────────────────────────────
print(f"🔄 Loading FAISS index from {INDEX_DIR} with deserialization enabled…")
# Force CPU usage for embeddings
embeddings = HuggingFaceEmbeddings(
    model_name=HF_EMBEDDING_MODEL,
    model_kwargs={'device': 'cpu'},
    encode_kwargs={'device': 'cpu'}
)
vector_store = FAISS.load_local(
    str(INDEX_DIR),
    embeddings,
    allow_dangerous_deserialization=True
)
print("✅ FAISS index loaded.")
retriever = vector_store.as_retriever(
    search_type="similarity",
    search_kwargs={"k": TOP_K}
)
print(f"🔄 Retriever initialized with top_k={TOP_K}")

# ─── DEFINE PROMPT TEMPLATE ─────────────────────────────────────────────────
template_str = """
You are an engineering assistant. Based on the following retrieved email or attachment contents, answer the user's question.
Be concise and cite each piece of information in the form [sender · subject · date · attachment/page if applicable].

{context}

Question: {question}
"""

prompt_template = PromptTemplate(
    input_variables=["context", "question"],
    template=template_str
)

# ─── BUILD RetrievalQA CHAIN ────────────────────────────────────────────────
qa = RetrievalQA.from_chain_type(
    llm=ChatOpenAI(model_name=LLM_MODEL, temperature=0.0),
    chain_type="stuff",
    retriever=retriever,
    return_source_documents=True,
    chain_type_kwargs={"prompt": prompt_template}
)

# ─── INTERACTIVE CLI ───────────────────────────────────────────────────────
def main():
    print(f"🔥 Using LLM model: {LLM_MODEL}")
    print("🔎 Enter your question (or 'exit' to quit):")
    while True:
        query = input(">>> ").strip()
        if not query or query.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break

        # ← use .invoke() instead of __call__ to avoid deprecation
        result = qa.invoke({"query": query})

        answer = result["result"]
        sources = result["source_documents"]

        # Print answer
        print("\n📖 Answer:")
        print(answer)

        # Print citations
        print("\n🗒️ Sources:")
        for doc in sources:
            md = doc.metadata
            parts = []
            if md.get("from"):
                parts.append(md["from"])
            if md.get("subject"):
                parts.append(md["subject"])
            if md.get("date"):
                parts.append(md["date"])
            if md.get("attachment"):
                page = md.get("page", "?")
                parts.append(f"{md['attachment']} p{page}")
            print(" - " + " · ".join(parts))

        print("\n🔎 Ask another question or type 'exit' to quit.")

if __name__ == "__main__":
    main()
