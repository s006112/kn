import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

RAG_DIR = Path(__file__).resolve().parent / "rag"
if str(RAG_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_DIR))

from rag.chunk_json import JsonlWriter


CLAUSE_HEADING_RE = re.compile(r"^(?P<num>\d+(?:\.\d+)*)(?:\s+(?P<title>.+))?$")


@dataclass
class StandardDocInfo:
    doc_id: str
    doc_code: str
    doc_type: str = "standard"
    metadata_json: dict | None = None


def _iter_clauses(lines: Iterable[str]) -> Iterable[Tuple[str, str, List[str]]]:
    """Yield (location_path, heading, lines) for each detected clause block."""
    current_loc = None
    current_heading = ""
    buffer: List[str] = []

    def flush():
        nonlocal buffer, current_loc, current_heading
        if current_loc and buffer:
            yield current_loc, current_heading, buffer
        buffer = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if buffer:
                buffer.append("")
            continue
        match = CLAUSE_HEADING_RE.match(line)
        if match:
            # New clause heading found; flush previous block first.
            yield from flush()
            current_loc = match.group("num")
            current_heading = (match.group("title") or "").strip()
            buffer = [line]
            continue
        if current_loc:
            buffer.append(line)
    # Flush last block
    yield from flush()


def chunk_standard_text(text: str, info: StandardDocInfo) -> List[Tuple[str, dict]]:
    """Chunk a standards text into clause-sized blocks with metadata."""
    lines = text.splitlines()
    chunks: List[Tuple[str, dict]] = []

    for location_path, heading, body_lines in _iter_clauses(lines):
        chunk_text = "\n".join(body_lines).strip()
        if not chunk_text:
            continue
        meta = {
            "doc_type": info.doc_type,
            "doc_id": info.doc_id,
            "doc_code": info.doc_code,
            "location_path": location_path,
            "heading": heading,
            "chunk_type": "clause",
        }
        if info.metadata_json:
            meta["metadata_json"] = info.metadata_json
        chunks.append((chunk_text, meta))
    return chunks


def write_chunks_to_jsonl(chunks: List[Tuple[str, dict]], output_path: Path) -> int:
    """Persist chunks to a JSONL file using the shared JsonlWriter."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with JsonlWriter(output_path) as writer:
        return writer.write_chunks(chunks)
