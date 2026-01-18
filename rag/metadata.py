#!/usr/bin/env python3
"""
Responsibility:
CLI utility script for inspecting `index/metadata.sqlite` by printing rows from the `chunks` table in a readable format.

Used by:
* (no direct callers found)

Pipelines:
- locate_db -> connect_sqlite -> query_rows -> print_fields -> close_connection

Invariants:
- Exits early when the database path does not exist.
- When `K` is not `None`, prints exactly one row using `LIMIT 1 OFFSET (K-1)`.

Out of scope:
- Building indexes or writing metadata.
- Any schema migration or validation.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("index/metadata.sqlite")
# Select only the Kth row (1-based); set to None to show all rows.
K = 5

if not DB_PATH.exists():
    raise SystemExit(f"Database not found: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
try:
    cur = conn.cursor()
    query = "SELECT * FROM chunks"
    params = ()
    if K is not None:
        query += " LIMIT 1 OFFSET ?"
        params = (max(K, 1) - 1,)
    rows = cur.execute(query, params)
    column_names = [desc[0] for desc in cur.description]
    for row in rows:
        for name, value in zip(column_names, row):
            print(f"{name}:\n{value}\n")
        print("-" * 80)
finally:
    conn.close()
