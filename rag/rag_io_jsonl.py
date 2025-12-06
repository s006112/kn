import json


def safe_read_jsonl_line(line_bytes, line_num):
    """Decode a JSONL line with fallbacks for encoding errors."""
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
