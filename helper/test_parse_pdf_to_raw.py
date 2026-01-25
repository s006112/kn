#!/usr/bin/env python3
import logging
import sys
from pathlib import Path

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[1]
HELPER_DIR = Path(__file__).resolve().parent
for _p in (str(ROOT_DIR), str(HELPER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RAW_PDF_DIR = Path("data/pdf_evaluation")
TXT_RAW_DIR = Path("data/pdf_evaluation/txt_raw")
LOG_FILE = Path("data/parse_pdf_to_raw.log")

def _configure_logging(log_file: Path) -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)


def _import_pdf_raw():
    import helper_parse_pdf_to_raw as pdf_raw

    return pdf_raw


def main():
    TXT_RAW_DIR.mkdir(parents=True, exist_ok=True)
    _configure_logging(LOG_FILE)
    pdf_raw = _import_pdf_raw()

    logging.info("=== PDF Raw Parsing Evaluation Run ===")
    logging.info("Log file: %s", LOG_FILE)

    for pdf in sorted(RAW_PDF_DIR.glob("*.pdf")):
        data = pdf.read_bytes()

        # Parsing (this will emit [PDF_PARSE_*] logs from helper)
        text = pdf_raw.get_pdf_full_text(data, pdf.name)

        out = TXT_RAW_DIR / (pdf.stem + ".txt")
        out.write_text(text, encoding="utf-8")

        logging.info(
            "[TXT_OUTPUT] file=%s, chars=%d, out=%s\n",
            pdf.name,
            len(text),
            out,
        )

    logging.info("=== Evaluation Complete ===")


if __name__ == "__main__":
    main()


class _FakePage:
    def __init__(self, text: str, has_images: bool) -> None:
        self._text = text
        self._has_images = has_images

    def get_text(self) -> str:
        return self._text

    def get_images(self, full: bool = False):
        return [("img",)] if self._has_images else []


class _FakeDoc:
    def __init__(self, pages: list[_FakePage]) -> None:
        self._pages = pages
        self.page_count = len(pages)

    def __iter__(self):
        return iter(self._pages)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_raw_extraction_returns_suspect_pages(monkeypatch) -> None:
    pdf_raw = _import_pdf_raw()
    pages = [
        _FakePage("x" * (pdf_raw.TEXT_LEN_THRESHOLD - 1), has_images=True),
        _FakePage("y" * (pdf_raw.TEXT_LEN_THRESHOLD + 10), has_images=True),
        _FakePage("z" * 10, has_images=False),
    ]
    monkeypatch.setattr(pdf_raw.fitz, "open", lambda *args, **kwargs: _FakeDoc(pages))

    extracted, suspect, form_pages, annot_pages = pdf_raw._raw_extraction(b"%PDF-1.4")
    assert set(extracted.keys()) == {1, 2, 3}
    assert suspect == {1, 2}
    assert form_pages == {}
    assert annot_pages == {}


def test_get_pdf_full_text_triggers_ocr_on_suspect_pages(monkeypatch) -> None:
    pdf_raw = _import_pdf_raw()
    pages = [
        _FakePage("x", has_images=True),
        _FakePage("a" * 300, has_images=False),
    ]
    monkeypatch.setattr(pdf_raw.fitz, "open", lambda *args, **kwargs: _FakeDoc(pages))

    calls: dict[str, int] = {"count": 0}

    def _fake_ocr(pages, page_sources, suspect_pages, doc):
        calls["count"] += 1
        pages[1] = "B" * 100
        page_sources[1] = "ocr"
        return pages, page_sources

    monkeypatch.setattr(pdf_raw, "_extract_text_with_ocr_fallback", _fake_ocr)

    out = pdf_raw.get_pdf_full_text(b"%PDF-1.4", filename="mixed.pdf")
    assert calls["count"] == 1
    assert out == ("B" * 100) + "\n" + ("a" * 300)


def test_get_pdf_full_text_does_not_trigger_ocr_without_suspect_or_missing(
    monkeypatch,
) -> None:
    pdf_raw = _import_pdf_raw()
    pages = [
        _FakePage("t" * (pdf_raw.TEXT_LEN_THRESHOLD + 1), has_images=False),
        _FakePage("u" * (pdf_raw.TEXT_LEN_THRESHOLD + 1), has_images=False),
    ]
    monkeypatch.setattr(pdf_raw.fitz, "open", lambda *args, **kwargs: _FakeDoc(pages))

    calls: dict[str, int] = {"count": 0}

    def _noop_ocr(pages, page_sources, suspect_pages, doc):
        calls["count"] += 1
        return pages, page_sources

    monkeypatch.setattr(pdf_raw, "_extract_text_with_ocr_fallback", _noop_ocr)

    out = pdf_raw.get_pdf_full_text(b"%PDF-1.4", filename="text.pdf")
    assert calls["count"] == 1
    assert out == ("t" * (pdf_raw.TEXT_LEN_THRESHOLD + 1)) + "\n" + (
        "u" * (pdf_raw.TEXT_LEN_THRESHOLD + 1)
    )


def test_get_pdf_page_blocks_emits_summary_log(monkeypatch, caplog) -> None:
    pdf_raw = _import_pdf_raw()
    pages = [
        _FakePage("hello", has_images=False),
        _FakePage("world", has_images=False),
    ]
    monkeypatch.setattr(pdf_raw.fitz, "open", lambda *args, **kwargs: _FakeDoc(pages))

    caplog.set_level(logging.INFO)
    blocks = pdf_raw.get_pdf_page_blocks(b"%PDF-1.4", filename="blocks.pdf")

    assert blocks == {
        1: [{"source": "raw", "text": "hello"}],
        2: [{"source": "raw", "text": "world"}],
    }
    assert any(
        "[PDF_PARSE_BLOCKS] file=blocks.pdf" in record.getMessage()
        and "form_blocks=" in record.getMessage()
        and "annot_blocks=" in record.getMessage()
        for record in caplog.records
    )


class _FakeWidget:
    def __init__(self, field_name: str, field_value: str) -> None:
        self.field_name = field_name
        self.field_value = field_value


class _FakeAnnot:
    def __init__(self, *, content: str = "", title: str = "", subject: str = "") -> None:
        self.info = {"content": content, "title": title, "subject": subject}


class _FakePageWithExtras(_FakePage):
    def __init__(
        self,
        text: str,
        has_images: bool,
        *,
        widgets: list[_FakeWidget] | None = None,
        annots: list[_FakeAnnot] | None = None,
    ) -> None:
        super().__init__(text=text, has_images=has_images)
        self._widgets = widgets or []
        self._annots = annots or []

    def widgets(self):
        return self._widgets

    def annots(self):
        return self._annots


def test_get_pdf_page_blocks_includes_form_and_annot_blocks(monkeypatch) -> None:
    pdf_raw = _import_pdf_raw()
    pages = [
        _FakePageWithExtras(
            "hello",
            has_images=False,
            widgets=[_FakeWidget("PO Number", "20595")],
            annots=[_FakeAnnot(content="Approved by QA")],
        )
    ]
    monkeypatch.setattr(pdf_raw.fitz, "open", lambda *args, **kwargs: _FakeDoc(pages))

    blocks = pdf_raw.get_pdf_page_blocks(b"%PDF-1.4", filename="extras.pdf")
    assert blocks == {
        1: [
            {"source": "raw", "text": "hello"},
            {"source": "form", "text": "PO Number: 20595"},
            {"source": "annot", "text": "Approved by QA"},
        ]
    }
