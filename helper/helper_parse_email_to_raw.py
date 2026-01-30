#!/usr/bin/env python3
from __future__ import annotations
import re
from typing import List
from helper_sanitize import sanitize_text

_THREAD_SPLIT_PATTERNS = [
    re.compile(r"^On .+ wrote:$", re.M),
    re.compile(r"^.*於 .* 寫道:$", re.M),
    re.compile(r"^Begin forwarded message:", re.M),
    re.compile(r"^From:\s.+\nSent:\s.+\nTo:\s.+\nSubject:", re.M),
]

def should_split_threads(text: str, *, char_threshold: int = 2000) -> bool:
    if len(text) < char_threshold:
        return False
    return any(p.search(text) for p in _THREAD_SPLIT_PATTERNS)

def split_threads(text: str) -> List[str]:
    cuts = []
    for pat in _THREAD_SPLIT_PATTERNS:
        for m in pat.finditer(text):
            cuts.append(m.start())
    if not cuts:
        return [text]
    cuts = sorted(set(cuts))
    cuts.append(len(text))
    blocks = []
    prev = 0
    for pos in cuts:
        chunk = text[prev:pos].strip()
        if chunk:
            blocks.append(chunk)
        prev = pos
    return blocks

_QUOTE_DEPTH_RE = re.compile(r"^(>+)\s*(.*)")

def _split_by_quote_depth(text: str):
    buckets = {}
    for line in text.splitlines():
        m = _QUOTE_DEPTH_RE.match(line)
        if m:
            depth = len(m.group(1))
            content = m.group(2)
        else:
            depth = 0
            content = line
        buckets.setdefault(depth, []).append(content)
    segments = []
    for depth in sorted(buckets.keys()):
        seg = "\n".join(buckets[depth]).strip()
        if seg:
            segments.append((depth, seg))
    return segments

def parse_email_to_raw_blocks(email, email_id):
    text_part = email.get_body(preferencelist=("plain", "html"))
    if not text_part:
        return []
    content = text_part.get_content()
    if not content:
        return []
    if should_split_threads(content):
        thread_blocks = split_threads(content)
    else:
        thread_blocks = [content]
    blocks = []
    page = 1
    for thread_text in thread_blocks:
        for depth, text in _split_by_quote_depth(thread_text):
            blocks.append({
                "doc_id": email_id,
                "text": sanitize_text(text),
                "page": page,
                "source": "mbox",
                "part": "body" if depth == 0 else "quote",
            })
            page += 1
    return blocks
