import mysql.connector

servername = "localhost"
username = "baltechind_kenny"
password = "Kenny123"
dbname = "baltechind_grow"


def connect_db():
    return mysql.connector.connect(
        host=servername,
        user=username,
        password=password,
        database=dbname,
    )


def query_describe_led_coe(conn):
    structure_sql = "DESCRIBE LED_CoE"
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(structure_sql)
        return cur.fetchall()
    finally:
        cur.close()


def query_cct_options(conn):
    cct_sql = "SELECT DISTINCT CCT FROM LED_CoE WHERE CCT IS NOT NULL ORDER BY CCT ASC"
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(cct_sql)
        return cur.fetchall()
    finally:
        cur.close()


def query_cri_options(conn):
    cri_sql = "SELECT DISTINCT CRI FROM LED_CoE WHERE CRI IS NOT NULL ORDER BY CRI ASC"
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(cri_sql)
        return cur.fetchall()
    finally:
        cur.close()


def query_led_candidates(conn, target_cct, target_cri):
    candidate_sql = "SELECT * FROM LED_CoE WHERE CCT = %s AND CRI = %s"
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(candidate_sql, (target_cct, target_cri))
        return cur.fetchall()
    finally:
        cur.close()
