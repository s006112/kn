Photometric Extraction App
==========================

This project hosts a Gradio interface (`gradio_per_report.py`) backed by a core module (`core_per_report.py`) that automates the extraction and summarisation of photometric PDF reports. Uploading a report triggers a multi-stage pipeline that produces a ready-to-share summary, extracts chromaticity coordinates, and syncs artefacts with Nextcloud.

Processing Pipeline
-------------------
- **PDF ingestion** – `handle_upload()` reads the uploaded file, uses `helper/helper_pdf.get_pdf_full_text()` to recover full text, and guards against empty or unreadable PDFs.
- **Prompt-driven analysis** – The raw text is fed to the shared LLM helper twice (`utils_llm.call_llm()` using a PER-specific model): first with `Prompt_md.txt` to obtain a structured markdown analysis, then with `Prompt_summary.txt` to condense that analysis into an executive summary.
- **Report assembly** – A templated header/ footer wraps the AI responses with branding, placeholder checkboxes, and a link back to the shared PDF on Nextcloud (`helper_nextcloud.upload_and_share_file(..., share=True)`).
- **CIE PNG coordination** – A deterministic timestamp defines the expected PNG artefact name so the frontend can later upload the rendered chart.
- **Chromaticity parsing** – `_extract_cct_xy()` walks the generated markdown, isolates the “Product category” table, and uses regex matching to pull numeric CIE 1931 x,y pairs for display in a Gradio dataframe.
- **State caching** – `LATEST_SUMMARY` keeps the combined report available for incremental updates once the CIE chart arrives.

Hidden Upload Bridge
--------------------
`upload_cie_png()` receives a base64 payload emitted by custom frontend JavaScript, writes a temporary PNG, and calls `helper_nextcloud.upload_and_share_file(..., share=True)` to place it under `/Documents/PER/CIE Chart`. When the share link returns, the function swaps the placeholder URI embedded in the cached summary with the public preview URL.

User Interface
--------------
The Gradio layout, built inside `gr.Blocks`, exposes only the essential controls (file upload, submit button, summary box, and a CIE canvas created with `cie1931.get_canvas_html()` / `get_drawing_javascript()`). Diagnostic elements such as the raw PDF text, the parsed x,y table, and the hidden PNG bridge textbox are suppressed unless `DEBUG_TEXTBOXES=true`. Launch configuration binds the app to `0.0.0.0:7860`.

Key Environment Inputs
----------------------
- `OPENAI_API_KEY` – required for the current LLM provider (for example `gpt-5-mini` for PER reports, `gpt-4.1-mini` for weekly summaries).
- `LOG_LEVEL` – adjusts logging verbosity (default `INFO`).
- `DEBUG_TEXTBOXES` – toggles visibility of debugging widgets.

Supporting Files
----------------
`Prompt_md.txt` and `Prompt_summary.txt` craft the AI prompts; `helper_nextcloud.py` provides sharing helpers; `utils_cie1931.py` generates the interactive colour space canvas. Together they enable a mostly hands-off workflow for turning raw photometric PDFs into polished reports.











Sales Order Importer
====================
`gradio_so_import.py` provides a Gradio-powered workflow that converts customer PO PDFs into structured sale orders for Odoo. The app extracts text from the upload, prompts an LLM to generate assignment-style Python code, and (when enabled) pushes the cleaned data and source PDF into Odoo.
End-to-End Flow
---------------
- **File intake** – `handle_upload()` validates the uploaded PDF and salesperson name, loads `Prompt_po.txt`, and invokes `get_pdf_full_text()` to recover PDF text.
- **AI parsing** – `utils_llm.call_llm()` merges the prompt and parsed text (using an SO-specific model) via a two-message payload, then asks the model for a `self.<field> = ...` style response. The function sanitizes the response, injects the provided salesperson, and returns both the generated code and raw PDF text for inspection.
- **Odoo integration** – When `ODOO_IMPORT=true`, `handle_upload()` passes the AI output to `create_sale_order_from_text()`. Successful imports trigger `attach_pdf_to_sale_order()` to archive the original PDF on the created record, share it to the partner-specific Nextcloud subfolder, and surface all status messages back to the UI.
- **Frontend** – A compact `gr.Blocks` layout exposes the PDF uploader, salesperson textbox, submit button, and read-only import log. Optional debugging textboxes (`DEBUG_TEXTBOXES=true`) show the AI response and extracted PDF text.
Module Highlights
-----------------
- `gradio_so_import.py` / `core_so_import.py`
  - Core logic in `core_so_import.py` handles PDF parsing, LLM prompting, optional Odoo calls, and user feedback; the Gradio script wires it to the UI.
- `utils_odoo.py`
  - `load_odoo_config()` and `get_odoo_client()` authenticate against the Odoo XML-RPC API and cache the client for reuse.
  - `find_id()` performs progressive record matching: exact lookups, prefix searches, wildcard searches, and normalized comparisons before selecting the best candidate deterministically.
  - `parse_po_response_text()` uses `ast` to safely interpret the AI-generated `self.field = value` statements, enforcing required fields and data types.
  - `create_sale_order()` builds the XML-RPC payload, normalizes dates, parses quantities, retries with a fallback company if necessary, and reads back the created order.
  - `attach_pdf_to_sale_order()` uploads the PDF as an `ir.attachment`, posts a note on the sale order, optionally shares the PDF to Nextcloud (per partner folder), and appends any share log messages to the running status log.
- `helper/helper_pdf.py`
  - `get_pdf_full_text()` uses PyMuPDF with automatic fallback to OCR (`ocrmypdf`) when no text is returned.
  - `extract_pdf_attachment_tasks()` supports services that need chunked PDF text.
Key Environment Variables
-------------------------
- `OPENAI_API_KEY` – LLM client authentication.
- `LOG_LEVEL` – Logging verbosity (default `INFO`).
- `DEBUG_TEXTBOXES` – Reveals debugging textboxes in the Gradio UI.
- `ODOO_IMPORT` – Enables sale order creation and PDF attachment.
- `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`, `ODOO_DEFAULT_COMPANY_NAME` – Required for Odoo connectivity and optional company fallback.
Running the Apps
----------------
Activate the relevant environment variables, then launch the Gradio interfaces:
```bash
# Photometric extraction (PER report)
python gradio_per_report.py       # listens on 0.0.0.0:7860

# Sales order importer
python gradio_so_import.py        # listens on 0.0.0.0:7960

# Weekly summary helper
python gui_weekly_summary.py      # listens on 0.0.0.0:1986

# Sketch-to-rendering tool
python gui_rendering.py
```
Set `ODOO_IMPORT=true` only when the Odoo credentials are configured and LLM responses are trusted for import.


Deployment Plan (Suggested)
---------------------------
- **Python environment**
  - Use a dedicated virtualenv (e.g. Python 3.10+), install dependencies with `pip install -r requirements.txt`.
  - Provide a `.env` file alongside the code with `OPENAI_API_KEY`, Nextcloud and Odoo credentials, and flags such as `DEBUG_TEXTBOXES` / `ODOO_IMPORT`.
- **Process layout**
- Run each Gradio app (`gradio_per_report.py`, `gradio_so_import.py`, `gui_weekly_summary.py`, `gui_rendering.py`) as a separate process, each bound to its documented port.
  - In production, supervise them via `systemd`, `supervisor`, or a process manager like `pm2` (through `python` commands).
- **Reverse proxy**
  - Place an Nginx or Apache reverse proxy in front, mapping friendly paths to each service, for example:
    - `/per` → `http://localhost:7860`
    - `/so-import` → `http://localhost:7960`
    - `/weekly` → `http://localhost:1986`
    - `/render` → `http://localhost:7861` (or another chosen port)
  - Terminate TLS at the proxy and keep Gradio apps on HTTP inside the network.
- **Configuration management**
  - Keep `.env` out of version control and maintain separate files per environment (dev/staging/prod) with the same variable names.
  - Use `LOG_LEVEL` to turn on more verbose logging (`DEBUG`) during troubleshooting without touching code.
- **Scaling and hardening**
  - For higher load, run multiple instances of a given app on different ports and load-balance at the reverse proxy layer.
  - Regularly rotate API keys and Nextcloud/Odoo credentials, and restrict network access so only the proxy and required APIs are reachable from the app hosts.
