"""
Responsibility:
Provides a small helper to decode a single JSONL line from bytes with encoding fallbacks and basic JSON validation.

Used by:
* rag/email_02_chunks_to_faiss.py

Pipelines:
- decode_bytes -> validate_json -> return_text -> return_warning

Invariants:
- Returns a `(text, warning)` tuple where `text` is either a stripped string or `None`.
- On the first encoding that successfully decodes and parses as JSON, `warning` is `None`.

Out of scope:
- Reading/writing JSONL files, batching, or streaming.
- Schema validation beyond `json.loads` parsing.
"""

import json


def safe_read_jsonl_line(line_bytes, line_num):
    """
    Purpose:
    Decode a JSONL line from bytes using a small set of encodings and confirm it parses as JSON.

    Inputs:
    - line_bytes: Raw bytes for a single line (including or excluding trailing newline).
    - line_num: Line number (currently unused).

    Outputs:
    - `(text, None)` when decoding succeeds and `json.loads(text)` succeeds for a tried encoding.
    - `(None, reason)` when decoding succeeds but JSON parsing fails for a tried encoding.
    - `(text, warning)` when decoding with replacement characters succeeds after fallback attempts.

    Side effects:
    - None.

    Failure modes:
    - Returns `(None, reason)` instead of raising for common decoding/JSON failures.
    - May still raise for unexpected exceptions outside the guarded decode attempts.
    """

    fallback_encodings = ['utf-8', 'windows-1252', 'iso-8859-1', 'cp1252', 'latin1']
    for enc in fallback_encodings:
        try:
            text = line_bytes.decode(enc).strip()
            json.loads(text)
            return text, None
        except (UnicodeDecodeError, LookupError):
            continue
        except json.JSONDecodeError:
            return None, f"invalid JSON with {enc}"
    try:
        text = line_bytes.decode('utf-8', errors='replace').strip()
        return text, "decoded with replacement chars"
    except Exception:
        return None, "encoding failed completely"
