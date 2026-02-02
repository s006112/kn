# build_faiss.py
from pathlib import Path

from faiss_index_builder import build_index


TARGET = "mbox" # "standard" | "mbox" | "rag"
FAISS_DIR = Path("data/faiss")
CHUNKS_PATH = Path(f"data/{TARGET}/jsonl/{TARGET}_chunks.jsonl")

def main():
    build_index(CHUNKS_PATH, FAISS_DIR, TARGET)


if __name__ == "__main__":
    main()
