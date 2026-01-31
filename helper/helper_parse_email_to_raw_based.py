#!/usr/bin/env python3
"""
helper_parse_email_to_raw_enhanced.py

Production-grade email → RawBlock parser
Clean linear pipeline (NO strategies, NO multi-pass splitting, NO conflicts)

Pipeline:

text
→ Phase-1 normalize_structure()        # insert missing '>' only (safe add)
→ Phase-0 split_by_quote_depth()       # RFC quote depth split only
→ Phase-2 segment_message_boundary()   # semantic message segmentation
→ Phase-3 cleanup_noise()              # remove junk
→ Phase-4 format_text()                # whitespace normalization

Design rules:
- each phase single responsibility
- no overlapping logic
- no multi-round split
- deterministic
"""

import re
from helper_sanitize import sanitize_text


# ============================================================
# Phase-1  Structure normalization (ONLY add, NEVER modify)
# ============================================================

# detect common “reply header” starts (missing '>' case)
_INSERT_BOUNDARY_RE = re.compile(
    r"(?im)^(?:"
    r"On .+ wrote:"
    r"|From:\s"
    r"|Sent:\s"
    r"|To:\s"
    r"|Cc:\s"
    r"|Subject:\s"
    r"|发件人："
    r"|发送时间："
    r"|收件人："
    r"|抄送："
    r"|主题："
    r"|[-]+\s*(?:轉寄郵件|转寄邮件|Forwarded message)\s*[-]+"
    r"|Begin forwarded message"
    r")"
)


def normalize_structure(text: str) -> str:
    """
    ONLY repair missing quote markers.

    If quote boundary detected but no leading '>',
    prepend '> ' until next blank line.

    Safe:
    - only add characters
    - never delete
    - monotonic
    """

    if not text:
        return ""

    lines = text.splitlines(True)
    out = []

    in_quote = False

    for line in lines:
        stripped = line.lstrip()

        if _INSERT_BOUNDARY_RE.match(stripped):
            in_quote = True

        if in_quote and not stripped.startswith(">"):
            out.append("> " + line)
        else:
            out.append(line)

        if stripped.strip() == "":
            in_quote = False

    return "".join(out)


# ============================================================
# Phase-0  RFC quote depth splitter (STRICT)
# ============================================================

_QUOTE_RE = re.compile(r"^(>+)\s?(.*)")


def split_by_quote_depth(text: str):
    """
    Split only by '>' depth.
    ZERO heuristics.
    """

    if not text:
        return []

    segments = []
    current_depth = None
    buf = []

    for raw in text.splitlines():
        m = _QUOTE_RE.match(raw)
        depth = len(m.group(1)) if m else 0
        content = m.group(2) if m else raw

        if current_depth is None:
            current_depth = depth
            buf.append(content)
            continue

        if depth == current_depth:
            buf.append(content)
        else:
            segments.append((current_depth, "\n".join(buf)))
            current_depth = depth
            buf = [content]

    if buf:
        segments.append((current_depth, "\n".join(buf)))

    return segments


# ============================================================
# Phase-2  Message boundary segmentation (ONE PASS ONLY)
# ============================================================

_BOUNDARY_RE = re.compile(
    r"(?im)^(?:"
    r"From:\s"
    r"|Sent:\s"
    r"|To:\s"
    r"|Cc:\s"
    r"|Subject:\s"
    r"|发件人："
    r"|发送时间："
    r"|收件人："
    r"|抄送："
    r"|主题："
    r"|Begin forwarded message"
    r"|On .+ wrote:"
    r"|[-]+\s*(?:轉寄郵件|转寄邮件|Forwarded message)\s*[-]+"
    r"|.*(?:寫道|写道)\s*[:：]\s*$"
    r")"
)

def segment_message_boundary(text: str):
    """
    Semantic segmentation only.
    Does NOT change content.
    Only split blocks.

    Handles:
    - Outlook
    - Apple Mail
    - Gmail plain text
    - Chinese clients
    """

    if not text.strip():
        return []

    lines = text.splitlines(True)

    cuts = [0]
    pos = 0

    for line in lines:
        probe = line.lstrip("> ").strip()

        if _BOUNDARY_RE.match(probe):
            cuts.append(pos)

        pos += len(line)

    if len(cuts) == 1:
        return [text.strip()]

    cuts.append(len(text))

    out = []
    for a, b in zip(cuts, cuts[1:]):
        seg = text[a:b].strip()
        if seg:
            out.append(seg)

    return out


# ============================================================
# Phase-3 cleanup
# ============================================================

def cleanup_noise(text: str) -> str:
    return sanitize_text(text)


# ============================================================
# Phase-4 format
# ============================================================

def format_text(text: str) -> str:
    return text.strip()


# ============================================================
# Main API  (DROP-IN)
# ============================================================

def parse_email_to_raw_blocks(email, email_id):

    body = email.get_body(preferencelist=("plain", "html"))
    if not body:
        return []

    content = body.get_content()
    if not content:
        return []

    # Phase-1
    content = normalize_structure(content)

    # Phase-0
    segments = split_by_quote_depth(content)

    blocks = []
    page = 1

    def emit(kind, txt):
        nonlocal page

        txt = cleanup_noise(txt)
        #txt = format_text(txt)

        if not txt:
            return

        blocks.append({
            "doc_id": email_id,
            "text": txt,
            "page": page,
            "source": "mbox",
            "part": kind,
        })

        page += 1

    for depth, text in segments:

        if depth == 0:
            emit("body", text)
        else:
            for seg in segment_message_boundary(text):
                emit("quote", seg)

    return blocks
