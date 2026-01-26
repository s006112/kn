#!/usr/bin/env python3
"""
helper/helper_pdf_to_semantics_schema.py

Responsibility:
Transform raw page blocks (from helper_parse_pdf_to_raw.py) into
a semantic document schema.

Input:
    blocks_by_page: dict[int, list[dict]]
        {
          1: [
              {"source": "raw", "text": "..."},
              {"source": "form", "text": "..."},
              {"source": "annot", "text": "..."},
          ],
          2: [...]
        }

Output:
    dict: semantic schema object
"""

from __future__ import annotations
from typing import Dict, List, Any


def build_semantic_schema(blocks_by_page: Dict[int, List[dict]]) -> Dict[str, Any]:
    """
    Minimal semantic schema builder.

    Current behavior:
    - Flatten all text blocks into a single linear corpus.
    - Keep page/source metadata.
    - No business understanding yet.

    This is only a data contract stub.
    """

    all_blocks = []
    full_text_lines = []

    for page, blocks in sorted(blocks_by_page.items()):
        for block in blocks:
            entry = {
                "page": page,
                "source": block["source"],
                "text": block["text"].strip(),
            }
            all_blocks.append(entry)
            full_text_lines.append(block["text"].strip())

    schema = {
        "meta": {
            "version": "0.1",
            "stage": "raw_semantic_stub",
            "total_pages": len(blocks_by_page),
            "total_blocks": len(all_blocks),
        },
        "corpus": "\n".join(full_text_lines),
        "blocks": all_blocks,
        "fields": {},        # 预留：Voltage, Model, Result, ...
        "tables": [],        # 预留：结构化表格
        "confidence": {},    # 预留：字段可信度评估
    }

    return schema
