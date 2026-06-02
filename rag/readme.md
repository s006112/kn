# rag Directory Guide

This document summarizes the role of every Python file under `rag/` as it exists today.

## ALI review pipeline

ALI pipeline 的 architecture contract、module ownership 和 safety invariants 统一维护在
`ali/README.md`。本文件只说明 `rag/` 目录内的 retrieval 相关模块。

## Retrieval and FAISS helpers

| File | Purpose |
| --- | --- |
| `faiss_build.py` | Minimal CLI entry point that triggers FAISS index construction from an existing chunks JSONL dataset. |
| `faiss_index_builder.py` | Builds a FAISS cosine-similarity index plus a SQLite metadata store from chunk JSONL. It batches texts, creates embeddings, writes vector metadata, and persists the final FAISS index files. |
| `helper_faiss_embedding.py` | Embedding wrapper around the BGE model. It loads the embedding model/tokenizer, generates normalized embeddings for texts, and includes a fallback path for model-format issues such as `.bin` to `safetensors` conversion. |
| `helper_query_rewriting.py` | Query-rewrite utility for retrieval. It generates a small set of retrieval-oriented query variants and merges multi-query candidate lists by max similarity score. |
| `helper_rag_pipeline.py` | Main RAG runtime. It loads chunk metadata and vectors, embeds questions, performs FAISS or brute-force KNN search, filters/deduplicates candidates, builds retrieval context, calls the LLM, and returns an answer plus similarity diagnostics. |
| `helper_sanitize.py` | Shared text-cleaning utilities. It sanitizes noisy text for chunking/LLM input, removes overlay/header junk from standards text exports, and inserts page-break markers for layout-aware preprocessing. |

## Mailbox export and ingestion scripts

| File | Purpose |
| --- | --- |
| `imap_to_mbox.py` | Exports messages from fixed IMAP folders into a local mbox file. It fetches mail since a configured date, preserves IMAP flags/folder info in custom headers, and writes a reproducible raw mailbox snapshot. |
| `imap_to_mbox_all_folder.py` | Variant of the IMAP exporter that first lists all folders, then fetches messages from every accessible folder into one local mbox file. |
| `parse_mbox_to_chunk.py` | End-to-end mbox ingestion script. It reads local mbox files, parses each email body into canonical blocks, optionally parses supported attachments, writes block JSONL, then derives chunk JSONL for indexing. |

## Parsing and chunking utilities

| File | Purpose |
| --- | --- |
| `parse_block_to_chunk.py` | Converts canonical block JSONL into retrieval chunks. It drops very short blocks, splits oversized blocks by paragraph/sentence/word budget, and writes chunk JSONL plus optional split/drop audit logs. |
| `parse_doc_helper.py` | Low-level Word extractor. It reads `.docx` files directly, tries external tools for legacy `.doc` files, sanitizes extracted text, and returns paragraph-indexed content. |
| `parse_doc_to_raw.py` | Wraps Word extraction into the project’s raw-block format. It converts paragraph-level text from `.doc` or `.docx` into `{index: [{source, text}]}` structures for downstream canonicalization. |
| `parse_email_to_raw.py` | Main email body parser. It normalizes quoted/forwarded history, optionally converts HTML blockquotes into RFC-style quote depth, splits email text into raw blocks by quote depth, sanitizes the results, and tags them for downstream JSONL conversion. |
| `parse_pdf_to_raw.py` | PDF text extractor with OCR fallback. It uses PyMuPDF to extract page text, supplements it with annotations/form fields/layout extraction, decides which pages need OCR, and returns page-indexed raw blocks plus source labels. |
| `parse_raw_to_jsonl.py` | Canonicalization layer for parsed content. It converts raw blocks from email, PDF, Word, and Excel sources into a uniform JSONL-ready block schema with metrics and metadata such as `doc_id`, `block_id`, `file_type`, and `source`. |
| `parse_standard_to_block.py` | Batch parser for standard-document PDFs. It walks `data/standard/pdf`, converts each PDF into canonical blocks, writes one per-file JSONL artifact, and also appends everything into a combined canonical JSONL file. |
| `parse_xls.py` | Excel text extractor. It reads workbook bytes, extracts and sanitizes sheet text, processes sheets in parallel when possible, and merges them into one attachment text body for downstream conversion. |
| `standard_txt_to_sanitized.py` | Preprocessor for standards TXT exports. It scans raw TXT files, removes overlay noise, inserts page-splitting markers, and writes cleaned `.page_splited` outputs for later processing. |

## Test and comparison scripts

| File | Purpose |
| --- | --- |
| `test_email_splitting.py` | Benchmark/comparison script for the baseline and enhanced email splitters. It runs both parsers on mbox messages, computes block-size metrics, and writes comparison records to JSONL. |
| `test_mbox_to_block.py` | Side-by-side evaluation pipeline for baseline vs enhanced email parsing. It converts mbox messages into canonical block JSONL for both variants and emits discrepancy collections for manual inspection. |
| `test_parse_email_to_raw_based.py` | Baseline version of the quote-depth email parser. It uses forwarded-header and quote-depth heuristics to split an email into raw blocks for comparison testing. |
| `test_parse_email_to_raw_enhanced.py` | Enhanced comparison version of the email parser. It improves quote/header handling and HTML blockquote normalization, then emits raw blocks for evaluation against the baseline parser. |
| `test_parse_raw_to_jsonl.py` | Test helper that canonicalizes raw email blocks produced by the baseline or enhanced parsers into a simplified block schema for comparison runs. |
| `test_save_email_raw_text.py` | Debug helper that saves raw email text bodies to `log/data/eml/` using a filesystem-safe filename derived from the message ID. |

## Notes

- `ali/README.md` defines the intended boundaries for the ALI email-review system.
- The `test_*.py` files are mostly evaluation/debug tooling rather than production pipeline modules.
