# db.py
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "led_coe_fallback.sqlite3")

def get_connection():
    return sqlite3.connect(DB_PATH)


def fetch_distinct_cct_cri():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT CCT, CRI FROM LED_CoE")
        return cur.fetchall()


def fetch_candidates_by_cct_cri(cct, cri):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM LED_CoE WHERE CCT = ? AND CRI = ?",
            (cct, cri)
        )
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        return [dict(zip(columns, row)) for row in rows]
