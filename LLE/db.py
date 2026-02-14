import re
import sqlite3
from pathlib import Path

import mysql.connector

servername = "localhost"
username = "baltechind_kenny"
password = "Kenny123"
dbname = "baltechind_grow"
_SQLITE_DB_PATH = Path(__file__).with_name("led_coe_fallback.sqlite3")
_SQL_DUMP_PATH = Path(__file__).with_name("LED_CoE.sql")


def _normalize_sqlite_col_type(col_type):
    ctype = col_type.strip()
    ctype = re.sub(r"int\(\d+\)", "INTEGER", ctype, flags=re.IGNORECASE)
    ctype = re.sub(r"varchar\(\d+\)", "TEXT", ctype, flags=re.IGNORECASE)
    ctype = re.sub(r"\bfloat\b", "REAL", ctype, flags=re.IGNORECASE)
    ctype = re.sub(r"decimal\(\d+\s*,\s*\d+\)", "REAL", ctype, flags=re.IGNORECASE)
    ctype = re.sub(r"\bdate\b", "TEXT", ctype, flags=re.IGNORECASE)
    ctype = re.sub(r"DEFAULT\s+current_timestamp\(\)", "DEFAULT CURRENT_TIMESTAMP", ctype, flags=re.IGNORECASE)
    ctype = re.sub(r"\s+COMMENT\s+'[^']*'", "", ctype, flags=re.IGNORECASE)
    return ctype.strip()


def _bootstrap_sqlite_from_dump(sqlite_conn):
    if not _SQL_DUMP_PATH.exists():
        raise FileNotFoundError(f"SQL dump not found: {_SQL_DUMP_PATH}")

    dump_text = _SQL_DUMP_PATH.read_text(encoding="utf-8", errors="ignore")
    create_match = re.search(r"CREATE TABLE `LED_CoE`\s*\((.*?)\)\s*ENGINE=", dump_text, re.DOTALL | re.IGNORECASE)
    if not create_match:
        raise RuntimeError("Could not find CREATE TABLE LED_CoE in SQL dump")

    col_defs = []
    for raw_line in create_match.group(1).splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or not line.startswith("`"):
            continue
        m = re.match(r"`([^`]+)`\s+(.+)$", line)
        if not m:
            continue
        col_name = m.group(1)
        col_type = _normalize_sqlite_col_type(m.group(2))
        if col_name == "ID":
            col_defs.append('"ID" INTEGER PRIMARY KEY')
        else:
            col_defs.append(f'"{col_name}" {col_type}')

    if not col_defs:
        raise RuntimeError("No columns parsed for LED_CoE")

    sqlite_conn.execute("DROP TABLE IF EXISTS LED_CoE")
    sqlite_conn.execute(f"CREATE TABLE LED_CoE ({', '.join(col_defs)})")

    insert_statements = re.findall(r"INSERT INTO `LED_CoE`.*?;", dump_text, re.DOTALL | re.IGNORECASE)
    if not insert_statements:
        raise RuntimeError("No INSERT statements found for LED_CoE")

    for stmt in insert_statements:
        sqlite_conn.executescript(stmt.replace("`", '"'))

    sqlite_conn.commit()


def _connect_sqlite_fallback():
    conn = sqlite3.connect(str(_SQLITE_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='LED_CoE' LIMIT 1"
        ).fetchone()
        if not has_table:
            _bootstrap_sqlite_from_dump(conn)
        return conn
    except Exception:
        conn.close()
        raise


def connect_db():
    try:
        return mysql.connector.connect(
            host=servername,
            user=username,
            password=password,
            database=dbname,
            connection_timeout=2,
        )
    except Exception:
        return _connect_sqlite_fallback()


def query_describe_led_coe(conn):
    if isinstance(conn, sqlite3.Connection):
        rows = conn.execute("PRAGMA table_info('LED_CoE')").fetchall()
        return [
            {
                "Field": row["name"],
                "Type": row["type"],
                "Null": "NO" if row["notnull"] else "YES",
                "Key": "PRI" if row["pk"] else "",
                "Default": row["dflt_value"],
                "Extra": "",
            }
            for row in rows
        ]

    structure_sql = "DESCRIBE LED_CoE"
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(structure_sql)
        return cur.fetchall()
    finally:
        cur.close()


def query_cct_options(conn):
    cct_sql = "SELECT DISTINCT CCT FROM LED_CoE WHERE CCT IS NOT NULL ORDER BY CCT ASC"
    if isinstance(conn, sqlite3.Connection):
        rows = conn.execute(cct_sql).fetchall()
        return [dict(row) for row in rows]

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(cct_sql)
        return cur.fetchall()
    finally:
        cur.close()


def query_cri_options(conn):
    cri_sql = "SELECT DISTINCT CRI FROM LED_CoE WHERE CRI IS NOT NULL ORDER BY CRI ASC"
    if isinstance(conn, sqlite3.Connection):
        rows = conn.execute(cri_sql).fetchall()
        return [dict(row) for row in rows]

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(cri_sql)
        return cur.fetchall()
    finally:
        cur.close()


def query_led_candidates(conn, target_cct, target_cri):
    if isinstance(conn, sqlite3.Connection):
        candidate_sql = "SELECT * FROM LED_CoE WHERE CCT = ? AND CRI = ?"
        rows = conn.execute(candidate_sql, (target_cct, target_cri)).fetchall()
        return [dict(row) for row in rows]

    candidate_sql = "SELECT * FROM LED_CoE WHERE CCT = %s AND CRI = %s"
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(candidate_sql, (target_cct, target_cri))
        return cur.fetchall()
    finally:
        cur.close()
