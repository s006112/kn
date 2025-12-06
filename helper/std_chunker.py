import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple, Optional

# 保留原來的結構，以免呼叫方壞掉
from rag.chunk_json import JsonlWriter  # noqa: F401


# 條文標題（主條文）：1、1.1、24.3 這類
CLAUSE_MAIN_RE = re.compile(
    r"^(?P<num>\d+(?:\.\d+)*)(?:\s+(?P<title>.+))?$"
)

# Supplement 條文：SA1、SA4.1、SB2.3 等
CLAUSE_SUPP_RE = re.compile(
    r"^(?P<prefix>S[A-Z])(?P<num>\d+(?:\.\d+)*)(?:\s+(?P<title>.+))?$"
)

# PART 行：PART 1 – ALL PRODUCTS
PART_RE = re.compile(
    r"^PART\s+(?P<num>\d+)\s*[-–]\s*(?P<title>.+)$",
    flags=re.IGNORECASE,
)

# SUPPLEMENT 行：SUPPLEMENT SA - FLUORESCENT BALLAST ACCESSORIES
SUPPLEMENT_RE = re.compile(
    r"^SUPPLEMENT\s+(?P<code>S[A-Z])\s*[-–]\s*(?P<title>.+)$",
    flags=re.IGNORECASE,
)

# APPENDIX 行：Appendix B Follow-Up Test for Printed Wiring Board Foil Trace
APPENDIX_RE = re.compile(
    r"^Appendix\s+(?P<code>[A-Z])\s+(?P<title>.+)$",
    flags=re.IGNORECASE,
)

# TOC 條目：24.3 Foil measurement ... .47 這種（尾巴是頁碼）
TOC_CLAUSE_LINE_RE = re.compile(
    r"^(?P<num>\d+(?:\.\d+)*)\s+"
    r"(?P<title>.+?)"
    r"\s+\.?(?P<page>\d{1,3})$"
)

# PDF 頁碼標記：<PARSED TEXT FOR PAGE: 1 / 108>
PARSED_PAGE_RE = re.compile(
    r"<PARSED TEXT FOR PAGE:\s*(?P<page>\d+)\s*/\s*\d+\s*>",
    flags=re.IGNORECASE,
)

# 顯然是 watermark / header 的行關鍵詞
WATERMARK_PATTERNS = (
    "Document Was Downloaded By",
    "ULSE INC. COPYRIGHTED MATERIAL",
)

NO_TEXT_LINE = "No Text on This Page"


@dataclass
class StandardDocInfo:
    doc_id: str
    doc_code: str
    doc_type: str = "standard"
    metadata_json: dict | None = None


@dataclass
class _ClauseBlock:
    location_path: str
    heading: str
    body_lines: List[str]
    part: Optional[str]
    supplement: Optional[str]
    appendix: Optional[str]
    page_start: Optional[int]
    page_end: Optional[int]


def _iter_clauses(lines: Iterable[str]) -> Iterable[_ClauseBlock]:
    """
    高階條文解析器：
    - 追蹤 PART / SUPPLEMENT / APPENDIX 上下文
    - 利用頁碼標記 <PARSED TEXT FOR PAGE: n / N> 追蹤 page_start/page_end
    - 過濾目錄 TOC 行（尾巴是頁碼的條文樣式行）
    - 過濾明顯的 watermark / header / No Text on This Page
    """
    current_part: Optional[str] = None
    current_supp: Optional[str] = None
    current_appx: Optional[str] = None

    current_loc: Optional[str] = None
    current_heading: str = ""
    buffer: List[str] = []

    current_page: Optional[int] = None
    clause_page_start: Optional[int] = None

    def flush():
        nonlocal buffer, current_loc, current_heading, clause_page_start
        if current_loc and buffer:
            yield _ClauseBlock(
                location_path=current_loc,
                heading=current_heading,
                body_lines=buffer[:],
                part=current_part,
                supplement=current_supp,
                appendix=current_appx,
                page_start=clause_page_start,
                page_end=current_page,
            )
        buffer = []
        clause_page_start = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            # 保留段落空行
            if buffer:
                buffer.append("")
            continue

        # 解析並去掉頁碼標記，如：<PARSED TEXT FOR PAGE: 3 / 108>
        m_page = PARSED_PAGE_RE.search(line)
        if m_page:
            try:
                current_page = int(m_page.group("page"))
            except Exception:
                pass
            line = PARSED_PAGE_RE.sub("", line).strip()
            if not line:
                # 此行只有頁碼標記
                continue

        # 濾掉明顯 watermark / header / “No Text on This Page”
        if any(pat in line for pat in WATERMARK_PATTERNS):
            continue
        if line == NO_TEXT_LINE:
            continue

        # PART / SUPPLEMENT / APPENDIX heading
        m_part = PART_RE.match(line)
        if m_part:
            current_part = f"PART {m_part.group('num')}"
            # PART 行本身不當條文
            continue

        m_supp = SUPPLEMENT_RE.match(line)
        if m_supp:
            current_supp = m_supp.group("code")
            current_appx = None
            # Supplement 行本身不當條文
            continue

        m_appx = APPENDIX_RE.match(line)
        if m_appx:
            current_appx = m_appx.group("code")
            current_supp = None
            # Appendix heading 也只作 context
            continue

        # TOC 條目：看起來像條文，但尾巴是頁碼 → 當作目錄，跳過
        if TOC_CLAUSE_LINE_RE.match(line):
            continue

        # Supplement 條文（SA1、SB3.4 ...）
        m_supp_clause = CLAUSE_SUPP_RE.match(line)
        if m_supp_clause:
            # 新條文，先 flush 舊條文
            yield from flush()
            prefix = m_supp_clause.group("prefix")  # 如 SA
            num = m_supp_clause.group("num")        # 如 4.1
            loc = f"{prefix}{num}"
            title = (m_supp_clause.group("title") or "").strip()
            current_loc = loc
            current_heading = title
            buffer = [line]
            clause_page_start = current_page
            continue

        # 主條文（1, 1.1, 24.3 ...）
        m_main = CLAUSE_MAIN_RE.match(line)
        if m_main:
            yield from flush()
            loc = m_main.group("num")
            title = (m_main.group("title") or "").strip()
            current_loc = loc
            current_heading = title
            buffer = [line]
            clause_page_start = current_page
            continue

        # 正常正文行：掛在當前條文下
        if current_loc:
            buffer.append(line)
        else:
            # 沒有 current_loc 的行（前言、版權等）暫時丟棄，
            # 如果你未來想保留，可額外做一個 pseudo-clause。
            continue

    # flush 最後一條
    yield from flush()


def chunk_standard_text(text: str, info: StandardDocInfo) -> List[Tuple[str, dict]]:
    """
    將「整本標準原文文本」切成「條文級 chunk」，並附帶結構 metadata。

    返回 list of (chunk_text, metadata)，供 JsonlWriter 直接寫入 JSONL。
    """
    lines = text.splitlines()
    chunks: List[Tuple[str, dict]] = []

    for block in _iter_clauses(lines):
        chunk_text = "\n".join(block.body_lines).strip()
        if not chunk_text:
            continue

        meta: dict = {
            "doc_type": info.doc_type,
            "doc_id": info.doc_id,
            "doc_code": info.doc_code,
            # 條文基本定位信息
            "location_path": block.location_path,
            "heading": block.heading,
            "chunk_type": "clause",
        }

        # 進一步的結構信息（便於後續檢索 / filter）
        if block.part:
            meta["part"] = block.part
        if block.supplement:
            meta["supplement"] = block.supplement  # 如 SA/SB/SC/SD
        if block.appendix:
            meta["appendix"] = block.appendix      # 如 A/B/C
        if block.page_start is not None:
            meta["page_start"] = block.page_start
        if block.page_end is not None:
            meta["page_end"] = block.page_end

        # 外部附加 metadata（如版本、語言等）
        if info.metadata_json:
            meta["metadata_json"] = info.metadata_json

        chunks.append((chunk_text, meta))

    return chunks


def write_chunks_to_jsonl(chunks: List[Tuple[str, dict]], output_path: Path) -> int:
    """保持原有 API，方便其他腳本重用。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with JsonlWriter(output_path) as writer:
        return writer.write_chunks(chunks)
