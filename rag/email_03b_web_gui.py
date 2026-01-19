#!/usr/bin/env python3
"""Gradio UI for querying email data via FAISS + embeddings.
Designed for Hugging Face Spaces (sdk: gradio).
Phase 0: keep functionality identical, no Pydantic, no langchain_core.output_parsers.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gradio as gr
from dotenv import load_dotenv
from helper.helper_rag_mbox import RagEngine
    
# ─── Environment setup ──────────────────────────────────────────────────────
os.environ.update({"CUDA_VISIBLE_DEVICES": "", "TORCH_USE_CUDA_DSA": "0"})  # force CPU
sys.modules["torchvision"] = None  # avoid accidental heavy imports
load_dotenv()  # load API keys if present

rag_engine = RagEngine()

def answer_question(raw_query: str):
    return rag_engine.answer_question(raw_query or "")

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
