#!/usr/bin/env python3
"""CLI RAG for technical standards (uses separate standards index)."""

import argparse
import sys
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent / "rag"
if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))

from rag.rag_embeddings import EmbeddingModel  # type: ignore  # noqa: E402
from rag.rag_retrieval import similarity_search_with_score  # type: ignore  # noqa: E402
from std_vectorstore import StdChunk, load_standard_index  # noqa: E402
from utils_llm import call_llm  # noqa: E402


INDEX_DIR = Path("data/index_std")
EMBED_MODEL = "jinaai/jina-embeddings-v3"
EMBED_BATCH_SIZE = 16
LLM_MODEL = "gpt-4.1-mini"
TOP_K = 20
SCORE_THRESHOLD = 0.35


def _format_snippet(chunk: StdChunk) -> str:
    meta = chunk.metadata or {}
    doc_code = meta.get("doc_code", meta.get("doc_id", "(doc)"))
    loc = meta.get("location_path", "(loc)")
    heading = meta.get("heading", "").strip()
    heading_part = f" — {heading}" if heading else ""
    return f"[{doc_code} {loc}{heading_part}]\n{chunk.page_content}"


def answer_standard_question(question: str) -> tuple[str, str]:
    if not question.strip():
        return "", ""

    index, docs_by_id = load_standard_index(INDEX_DIR)
    embedder = EmbeddingModel(
        model_name=EMBED_MODEL,
        device="cpu",
        batch_size=EMBED_BATCH_SIZE,
        task="retrieval.query",
    )

    q_vec = embedder.embed_query(question.strip())
    hits = similarity_search_with_score(index, docs_by_id, q_vec, k=TOP_K)

    filtered = [(doc, score) for doc, score in hits if score >= SCORE_THRESHOLD]
    if not filtered and hits:
        filtered = [hits[0]]

    snippets = [_format_snippet(doc) for doc, _ in filtered]
    context = "\n\n".join(snippets)
    prompt = f"Refer to the following clauses and answer the question with citations to clause numbers:\n{context}\n\nQuestion: {question.strip()}"

    result_text = call_llm(
        LLM_MODEL,
        system_prompt="You are a technical standards assistant. Cite clause numbers and table IDs in your answers.",
        user_text=prompt,
        max_retries=2,
    )

    table_lines = ["| score | doc | clause | heading |", "|---:|---|---|---|"]
    for doc, score in hits:
        meta = doc.metadata or {}
        doc_code = meta.get("doc_code", "(doc)")
        loc = meta.get("location_path", "(loc)")
        heading = str(meta.get("heading", "")).replace("|", "\\|")
        table_lines.append(f"| {score:.4f} | {doc_code} | {loc} | {heading} |")
    sources_md = "\n".join(table_lines)

    return result_text.strip(), sources_md


def main(argv=None) -> int:
    default_q = "一款仅适用于橱柜底部安装的 under-cabinet 灯具，根据 UL 1598，铭牌或标签上必须有哪一句或哪些安装适用性标示？这些标示文字在哪一个条款和哪一张表格中规定？Only reply in Chinese"
    parser = argparse.ArgumentParser(description="Ask a question against the standards index.")
    parser.add_argument(
        "question",
        type=str,
        nargs="*",
        help="Question to ask",
    )
    args = parser.parse_args(argv)
    question = " ".join(args.question) if args.question else default_q

    answer, sources = answer_standard_question(question)
    print("\n=== Answer ===\n")
    print(answer)
    print("\n=== Top hits ===\n")
    print(sources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
