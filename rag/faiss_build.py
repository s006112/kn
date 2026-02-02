from pathlib import Path
from faiss_index_builder import build_index

TARGET = "mbox"

ROOT_DIR = Path(__file__).resolve().parent.parent  # ← 关键
DATA_DIR = ROOT_DIR / "data"

FAISS_DIR = DATA_DIR / "faiss"
CHUNKS_PATH = DATA_DIR / TARGET / "jsonl" / f"{TARGET}_chunks.jsonl"

def main():
    build_index(CHUNKS_PATH, FAISS_DIR, TARGET)

if __name__ == "__main__":
    main()
