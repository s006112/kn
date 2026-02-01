# build_faiss.py
from pathlib import Path

from helper_faiss_chunk_cleaner import load_and_clean_chunks
from helper_faiss_index_builder import build_index

TARGET = "mbox" # "standard" | "mbox" | "rag"

JSON_DIR = Path(f"data/{TARGET}/jsonl")
FAISS_DIR = Path("data/faiss")
BLOCK_SUFFIX = "blocks.jsonl"


def main():
    chunks = load_and_clean_chunks(JSON_DIR, BLOCK_SUFFIX)
    build_index(chunks, FAISS_DIR, TARGET)


if __name__ == "__main__":
    main()
