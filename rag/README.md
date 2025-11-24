---
title: Email RAG Demo
emoji: 👀
colorFrom: blue
colorTo: yellow
sdk: gradio
sdk_version: 5.43.1
app_file: 04_web_hf.py
pinned: false
license: apache-2.0
header: mini           # <-- makes the header minimal
fullWidth: true        # make your app utilize full width
---


Vivid quotation reference
T8GU series quotation reference
LDM-390-46-AL-M
Acuity Swivel Design project summary


Check out the configuration reference at https://huggingface.co/docs/hub/spaces-config-reference


# Email RAG Demo

This repository demonstrates how to build a retrieval‑augmented generation (RAG)
system for your personal email archive. After extracting and indexing your
messages you can query them either from the command line or via a small web
application.

## Scripts

- `01_extract.py` – extract emails and attachments
- `02_build_index.py` – build a FAISS vector index
- `03_ask.py` – command line interface for asking questions
- `web_ask.py` – Flask application that exposes a browser interface

## Quick Start

1. (Optional) Create and activate a virtual environment
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install the requirements
   ```bash
   pip install -r requirements.txt
   sudo apt update
   sudo apt install antiword    # 优先用于 .doc 提取
   sudo apt install catdoc     # 作为 fallback
   ```
3. Configure your IMAP credentials in a `.env` file and run the extractor
   ```bash
   python3 01_extract.py
   ```
4. Build the index
   ```bash
   python3 02_build_index.py
   ```
5. Start the web interface
   ```bash
   python3 web_ask.py
   ```
6. Open your browser to `http://localhost:5000` and ask questions.

The page will display the answer with Markdown formatting and include
citations from your email data.
