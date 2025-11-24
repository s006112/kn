from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from utils_config import configure_logging, get_env_flag, load_env, load_prompt_text
from utils_odoo import attach_pdf_to_sale_order, create_sale_order_from_text
from utils_llm import run_prompt
from utils_pdf import extract_text_from_pdf_bytes

load_env()
log = configure_logging("so_import")
_ODOO_IMPORT_ENABLED = get_env_flag("ODOO_IMPORT", False)
_PO_RESPONSE_DEBUG = get_env_flag("PO_RESPONSE_DEBUG", False)
_DEBUG_PDF_PARSING_TEXT = os.getenv("pdf_parsing_text", "")
LLM_MODEL = "gpt-5-mini"


class _ImportLogHandler(logging.Handler):
    """Capture log records and format them for display in the import log textbox."""

    def __init__(self, collector: list[str]):
        super().__init__()
        self._collector = collector
        self._saw_warning = False
        self.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._collector.append(self.format(record))
        except Exception:
            self._collector.append(record.getMessage())
        if record.levelno >= logging.WARNING:
            self._saw_warning = True

    @property
    def saw_warning(self) -> bool:
        return self._saw_warning


@dataclass
class SOImportResult:
    po_response_text: str
    pdf_parsing_text: str
    import_log: str
    sale_order_link_url: str


def run_so_import(file_path: str | None, salesperson: str) -> SOImportResult:
    """
    Core handler for SO import processing.

    Returns:
      SOImportResult(po_response_text, pdf_parsing_text, import_log, sale_order_link_url)
    """
    if not salesperson or not salesperson.strip():
        return SOImportResult("Error: Sales person is required.", "", "", "")

    salesperson_value = salesperson.strip()
    pdf_parsing_text = ""

    if not _PO_RESPONSE_DEBUG:
        if not file_path or not os.path.isfile(file_path):
            return SOImportResult("Error: No file found.", "", "", "")

    if _PO_RESPONSE_DEBUG:
        pdf_parsing_text = _DEBUG_PDF_PARSING_TEXT
        if not pdf_parsing_text:
            return SOImportResult("Error: Debug mode requires pdf_parsing_text in .env.", "", "", "")
    else:
        assert file_path is not None
        with open(file_path, "rb") as f:
            data = f.read()

        pdf_pages = extract_text_from_pdf_bytes(data, Path(file_path).name)
        if not pdf_pages:
            return SOImportResult("Error: PDF parsing failed.", "", "", "")
        pdf_parsing_text = "\n\n".join(
            f"[Page {page_no}]\n{text.strip()}"
            for page_no, text in sorted(pdf_pages.items())
            if text and text.strip()
        )
        if not pdf_parsing_text:
            return SOImportResult("Error: PDF parsing produced empty text.", "", "", "")

    base_dir = Path(__file__).parent
    prompt_po_str = load_prompt_text(base_dir, "Prompt_po.txt")
    if not prompt_po_str:
        return SOImportResult("Error: Failed to load Prompt_po.txt", "", "", "")

    try:
        llm_po_response = run_prompt(
            prompt_po_str,
            pdf_parsing_text,
            model=LLM_MODEL,
            multi_message=True,
        )
    except Exception as exc:
        return SOImportResult(f"Error querying LLM: {exc}", pdf_parsing_text, "", "")
    import_messages: list[str] = []
    created_order_name: str | None = None
    created_order_id: str | None = None

    if llm_po_response and not llm_po_response.startswith("Error"):
        lines_without_salesperson = [
            line for line in llm_po_response.splitlines() if not line.strip().startswith("self.salesperson")
        ]
        sanitized_response = "\n".join(line for line in lines_without_salesperson if line.strip())
        salesperson_literal = json.dumps(salesperson_value)
        header_line = f"self.salesperson = {salesperson_literal}"
        llm_po_response = f"{header_line}\n{sanitized_response}" if sanitized_response else header_line
        if _ODOO_IMPORT_ENABLED:
            try:
                collected_logs: list[str] = []
                odoo_logger = logging.getLogger("utils_odoo")
                import_log_handler = _ImportLogHandler(collected_logs)
                odoo_logger.addHandler(import_log_handler)
                order_id, order_data = create_sale_order_from_text(llm_po_response)

                order_name = ""
                if isinstance(order_data, dict):
                    order_name = str(order_data.get("name") or "").strip()
                if order_name:
                    created_order_name = order_name
                if order_id is not None:
                    created_order_id = str(order_id)
                if not order_name:
                    log.error("Missing sale order name for order %s; skipping attachment.", order_id)
                    import_messages.append("Attachment skipped: missing sale order name from Odoo response.")
                else:
                    try:
                        if file_path and os.path.isfile(file_path):
                            attach_pdf_to_sale_order(
                                sale_order_identifier=order_name,
                                pdf_path=file_path,
                                note_body="Attached customer PO",
                                upload_to_nextcloud=not _PO_RESPONSE_DEBUG,
                                status_log=import_messages,
                            )
                        else:
                            import_messages.append("Attachment skipped: no PDF file provided.")
                    except Exception as attach_exc:
                        log.exception(
                            "Failed to attach PDF '%s' to sale order %s: %s",
                            file_path,
                            order_name,
                            attach_exc,
                        )
                        import_messages.append(f"Attachment failed: {attach_exc}")
            except Exception as exc:
                log.exception("Odoo sale order creation failed: %s", exc)
                import_messages.append(f"Odoo sale order creation failed: {exc}")
            finally:
                saw_warning = import_log_handler.saw_warning
                odoo_logger.removeHandler(import_log_handler)
                import_log_handler.close()
                if collected_logs:
                    unique_messages: list[str] = []
                    for entry in import_messages + collected_logs:
                        if entry and entry not in unique_messages:
                            unique_messages.append(entry)
                    import_messages = unique_messages
                    if saw_warning:
                        log_path = Path(__file__).with_name("app_so_import.log")
                        new_content = "\n".join(import_messages)
                        if new_content:
                            new_content = f"{new_content}\n"
                        if log_path.exists():
                            existing = log_path.read_text(encoding="utf-8")
                            merged = f"{new_content}\n{existing}" if existing else new_content
                        else:
                            merged = new_content
                        log_path.write_text(merged, encoding="utf-8")
        else:
            import_messages.append("Odoo import skipped: ODOO_IMPORT flag is not set to true.")

    sale_order_link_url = ""
    if created_order_name and created_order_id:
        sale_order_link_url = f"https://ampco.odoo.com/odoo/sales/{created_order_id}"
        import_log_message = f"{created_order_name} \n"
    elif import_messages:
        import_log_message = "\n".join(import_messages)
    else:
        import_log_message = ""

    return SOImportResult(llm_po_response, pdf_parsing_text, import_log_message, sale_order_link_url)


__all__ = ["SOImportResult", "run_so_import"]
