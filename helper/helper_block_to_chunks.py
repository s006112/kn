# chunk_cleaner.py
import os
import json
import re
from glob import glob
from pathlib import Path

# =========================
# config
# =========================

SAFE_BATCH = 16
HARD_MIN_WORDS = 10          # Rule 1: word <= 2 -> drop
SOFT_SHORT_WORDS = 10        # Rule 2: word < 8 and low-information -> drop
MAX_SPLIT_WORDS = 800       # Rule 3: word > MAX_SPLIT_WORDS -> split (never drop long)

_EN_STOPWORDS = {
    "a","an","the","and","or","but",
    "i","me","my","myself","we","our","ours","ourselves",
    "you","your","yours","yourself","yourselves",
    "he","him","his","himself","she","her","hers","herself",
    "it","its","itself","they","them","their","theirs","themselves",
    "this","that","these","those",
    "is","am","are","was","were","be","been","being",
    "have","has","had","having","do","does","did","doing",
    "to","of","in","on","for","with","as","at","by","from","into","about","over","under",
    "not","no","yes","ok","okay",
    "so","too","very","just","only",
}

_LOW_INFO_REGEXES = [
    re.compile(r"^(?:wrote|hi|hello|hey)[!. ,]*$", re.I),
    re.compile(r"^(?:ok|okay|k|noted|received|got it|roger|ack)[!. ,]*$", re.I),
    re.compile(r"^(?:thanks|thank you|thx|tks)[!. ,]*$", re.I),
    re.compile(r"^(?:best regards|kind regards|regards|br|cheers|sincerely)[!. ,]*$", re.I),
    re.compile(r"^(?:sent from my .+)$", re.I),
    re.compile(r"^(?:--+)$"),
    # 中文常见“无信息”短句 / 签名
    re.compile(r"^(?:谢谢|多谢|感谢|謝謝|多謝|感謝)[!！。．、,， ]*$"),
    re.compile(r"^(?:寫道|收到|已收到|收悉|悉知|已阅|已讀|已讀取|已了解|了解|明白|好的|好|可以|没问题|沒問題|OK|Ok|ok)[!！。．、,， ]*$"),
    re.compile(r"^(?:请了解|請了解|请知悉|請知悉|请查收|請查收|烦请查收|煩請查收|敬请查收|敬請查收)[!！。．、,， ]*$"),
    re.compile(r"^(?:收到|已收到|收悉|悉知)[!！。．、,， ]*(?:谢谢|多谢|感谢|謝謝|多謝|感謝)?[!！。．、,， ]*$"),
    re.compile(r"^(?:请了解|請了解|请知悉|請知悉|请查收|請查收|烦请查收|煩請查收|敬请查收|敬請查收)[!！。．、,， ]*(?:谢谢|多谢|感谢|謝謝|多謝|感謝)?[!！。．、,， ]*$"),
    re.compile(r"^(?:此致|敬礼|敬禮|祝好|順祝商祺|顺祝商祺|致礼|致禮|敬上)[!！。．、,， ]*$"),
    re.compile(r"^(?:发自我的iPhone|發自我的iPhone|发自我的手机|發自我的手機).*$"),
]


def _safe_int(v):
    try:
        return int(v)
    except Exception:
        return None


def _count_words(text: str) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    return len(t.split())


def _is_low_information(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True

    normalized = re.sub(r"\s+", " ", t).strip()
    for rx in _LOW_INFO_REGEXES:
        if rx.match(normalized):
            return True

    lower = normalized.lower()
    tokens = re.findall(r"[a-z]+(?:'[a-z]+)?", lower)
    if tokens and all(tok in _EN_STOPWORDS for tok in tokens):
        return True

    if not re.search(r"[a-z]", lower):
        if re.fullmatch(r"[\d\W_ ]+", lower):
            return True

    return False


def _split_long_text(text: str, *, max_words: int, word_count_hint: int | None = None) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if word_count_hint is not None:
        if word_count_hint <= max_words:
            return [t]
    elif _count_words(t) <= max_words:
        return [t]

    def pack_units(units: list[str]) -> list[str]:
        out: list[str] = []
        cur: list[str] = []
        cur_words = 0
        for u in units:
            u = u.strip()
            if not u:
                continue
            w = _count_words(u)
            if w > max_words:
                if cur:
                    out.append(" ".join(cur).strip())
                    cur, cur_words = [], 0
                out.extend(_split_long_text(u, max_words=max_words, word_count_hint=w))
                continue
            if cur_words and (cur_words + w) > max_words:
                out.append(" ".join(cur).strip())
                cur, cur_words = [], 0
            cur.append(u)
            cur_words += w
        if cur:
            out.append(" ".join(cur).strip())
        return [x for x in out if x]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", t) if p.strip()]
    if len(paragraphs) > 1:
        return pack_units(paragraphs)

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", t)) if s.strip()]
    if len(sentences) > 1:
        return pack_units(sentences)

    words = t.split()
    if not words:
        return []
    out = []
    for i in range(0, len(words), max_words):
        out.append(" ".join(words[i : i + max_words]).strip())
    return [x for x in out if x]

# ==========================================

def _dump_chunks_jsonl(chunks, out_path: Path):
    with open(out_path, "w", encoding="utf-8") as f:
        for text, meta in chunks:
            #obj = {"text": text, **meta}
            obj = {**meta, "text": text}
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def build_chunks_jsonl(json_dir: Path, block_suffix: str, out_path: Path):
    pattern = os.path.join(json_dir, f"*{block_suffix}")
    files = glob(pattern)

    chunks = []
    stats = {
        "drop_hard": 0,
        "drop_soft": 0,
        "split_blocks": 0,
        "split_added": 0,
    }

    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)

                text = (obj.get("text") or "").strip()
                if not text:
                    continue

                word = _count_words(text)

                if word <= HARD_MIN_WORDS:
                    stats["drop_hard"] += 1
                    continue

                if word < SOFT_SHORT_WORDS and _is_low_information(text):
                    stats["drop_soft"] += 1
                    continue

                meta = obj

                if word > MAX_SPLIT_WORDS:
                    subs = _split_long_text(text, max_words=MAX_SPLIT_WORDS, word_count_hint=word)
                    stats["split_blocks"] += 1
                    for sub in subs:
                        chunks.append((sub, meta))
                        stats["split_added"] += 1
                    continue

                chunks.append((text, meta))

    _dump_chunks_jsonl(chunks, out_path)

    print(
        f"CLEAN STATS: {stats}\n"
        f"CHUNKS WRITTEN → {out_path}"
    )

