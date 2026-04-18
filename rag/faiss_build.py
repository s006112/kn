from pathlib import Path
from faiss_index_builder import build_index

TARGET = "mbox"

ROOT_DIR = Path(__file__).resolve().parent.parent  # ← 关键
DATA_DIR = ROOT_DIR / "data"

FAISS_DIR = DATA_DIR / "faiss"
JSONL_DIR = DATA_DIR / TARGET / "jsonl"

def main():
    for chunks_path in sorted(JSONL_DIR.glob("*_chunks.jsonl")):
        name = chunks_path.stem[:-7]
        build_index(chunks_path, FAISS_DIR, name)

if __name__ == "__main__":
    main()
