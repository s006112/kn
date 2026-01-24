#!/usr/bin/env python3
import sys
import logging
from pathlib import Path

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

RAW_PDF_DIR = Path("data/pdf_evaluation")
TXT_RAW_DIR = Path("data/pdf_evaluation/txt_raw")
TXT_RAW_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = TXT_RAW_DIR / "parse_pdf_to_raw.log"

# ----------------------------------------------------------------------
# Logging: stdout + file
# ----------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

formatter = logging.Formatter("%(message)s")

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)

# File handler
file_handler = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
file_handler.setFormatter(formatter)

# Avoid duplicated handlers if script is reloaded
if not logger.handlers:
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
else:
    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

# ----------------------------------------------------------------------
# Import after logging is configured so helper logs are captured
# ----------------------------------------------------------------------
from helper_parse_pdf_to_raw import get_pdf_full_text


def main():
    logging.info("=== PDF Raw Parsing Evaluation Run ===")
    logging.info("Log file: %s", LOG_FILE)

    for pdf in sorted(RAW_PDF_DIR.glob("*.pdf")):
        data = pdf.read_bytes()

        # Parsing (this will emit [PDF_PARSE_*] logs from helper)
        text = get_pdf_full_text(data, pdf.name)

        out = TXT_RAW_DIR / (pdf.stem + ".txt")
        out.write_text(text, encoding="utf-8")

        logging.info(
            "[TXT_OUTPUT] file=%s, chars=%d, out=%s",
            pdf.name,
            len(text),
            out,
        )

    logging.info("=== Evaluation Complete ===")


if __name__ == "__main__":
    main()
