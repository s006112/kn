#!/usr/bin/env python3
"""Print contents of index/metadata.sqlite in a readable form."""

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
