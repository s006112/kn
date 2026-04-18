#!/usr/bin/env python3
# imap_to_mbox_all_folder.py
import os
import ssl
import imaplib
import mailbox
from email import message_from_bytes
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timedelta

# ============================================================
# KEEP YOUR CONFIG (DO NOT TOUCH)
# ============================================================

DEFAULT_SERVER = "mail.ampco.com.hk"
DEFAULT_PORT = 993
DEFAULT_TIMEOUT = 300
DEFAULT_SINCE_DATE = "2026-01-01"
DEFAULT_END_DATE = "2026-02-25"    # "Today", or YYYY-MM-DD"
DEFAULT_OUT_DIR = Path("data/mbox/raw")
DEFAULT_STATE_PATH = Path("data/mbox/raw/imap_state.json")
DEFAULT_CHUNK_SIZE = 100
DISCOVER_ALL_FOLDERS = True
DEFAULT_FOLDERS = ("INBOX",)
IGNORE_FOLDERS = ("Trash","Junk")

# ============================================================
# LOAD ACCOUNT FROM .env
# ============================================================

load_dotenv()
IMAP_USERNAME = os.getenv("IMAP_USERNAME")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER", DEFAULT_SERVER)
IMAP_PORT = int(os.getenv("IMAP_PORT", DEFAULT_PORT))

if not IMAP_USERNAME or not IMAP_PASSWORD:
    raise RuntimeError("IMAP_USERNAME / IMAP_PASSWORD not found in .env")

# ============================================================
# HELPERS
# ============================================================

def to_imap_date(d):
    return datetime.strptime(d, "%Y-%m-%d").strftime("%d-%b-%Y")

def build_search_criteria(since_date, end_date):
    if end_date == "Today":
        return f"SINCE {to_imap_date(since_date)}"

    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    before_date = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"SINCE {to_imap_date(since_date)} BEFORE {to_imap_date(before_date)}"

def parse_flags_from_line(line):
    i = line.find("FLAGS (")
    if i == -1:
        return []
    j = line.find(")", i)
    raw = line[i + 7 : j].strip()
    return raw.split()

def _progress_bar(done: int, total: int):
    width = 100
    ratio = done / total if total else 1.0
    filled = int(width * ratio)
    bar = "█" * filled + "-" * (width - filled)
    print(f"\r[{bar}] {done}/{total}", end="", flush=True)

def list_folders(imap):
    typ, data = imap.list()
    if typ != "OK":
        raise RuntimeError("IMAP LIST failed")
    folders = []
    for line in data:
        # example: b'(\\HasNoChildren) "/" "INBOX/Sub"'
        parts = line.decode(errors="ignore").split(' "/" ')
        if len(parts) == 2:
            folder = parts[1].strip().strip('"')
            folders.append(folder)
    return folders

def fetch_folder(imap, folder, search_criteria, mbox):
    typ, _ = imap.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        print(f"[!] SKIP folder {folder}")
        return 0

    typ, data = imap.uid("SEARCH", None, search_criteria)
    uids = data[0].decode().split() if data and data[0] else []
    print(f"[+] {folder}: {len(uids)} mails for {search_criteria}")

    count = 0

    for i, uid in enumerate(uids, start=1):
        typ, resp = imap.uid("FETCH", uid, "(UID FLAGS BODY.PEEK[])")
        if typ != "OK":
            print(f"[!] FETCH FAIL UID {uid} in {folder}")
            _progress_bar(i, len(uids))
            continue

        flags = []
        raw_mail = None

        for line in resp:
            if not line:
                continue

            if isinstance(line, tuple):
                header, body = line
                try:
                    header_text = header.decode(errors="ignore")
                except:
                    header_text = str(header)

                if "FLAGS (" in header_text:
                    flags = parse_flags_from_line(header_text)

                if isinstance(body, (bytes, bytearray)):
                    raw_mail = bytes(body)
            else:
                try:
                    text = line.decode(errors="ignore")
                except:
                    text = str(line)
                if "FLAGS (" in text:
                    flags = parse_flags_from_line(text)

        if raw_mail is None:
            print(f"[!] NO BODY UID {uid} in {folder}")
            _progress_bar(i, len(uids))
            continue

        msg = message_from_bytes(raw_mail)
        m = mailbox.mboxMessage(msg)

        m["X-IMAP-UID"] = uid
        m["X-IMAP-Folder"] = folder
        m["X-IMAP-Flags"] = " ".join(flags)
        m["X-IMAP-Flags-Count"] = str(len(flags))

        mbox.add(m)
        count += 1

        if any(f.startswith("$label") for f in flags):
            print(f"[HIT] {folder} UID {uid} FLAGS {flags}")

        _progress_bar(i, len(uids))

    if uids:
        print()

    return count

# ============================================================
# MAIN
# ============================================================

def main():
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")

    imap = imaplib.IMAP4_SSL(
        IMAP_SERVER,
        IMAP_PORT,
        ssl_context=ctx,
        timeout=DEFAULT_TIMEOUT,
    )

    print("[+] CONNECTED")
    imap.login(IMAP_USERNAME, IMAP_PASSWORD)
    print("[+] LOGGED IN")

    if DISCOVER_ALL_FOLDERS:
        folders = list_folders(imap)
        print(f"[+] FOUND {len(folders)} folders")
    else:
        folders = list(DEFAULT_FOLDERS)
        print(f"[+] USING FIXED FOLDERS: {folders}")

    folders = [folder for folder in folders if folder not in IGNORE_FOLDERS]

    search_criteria = build_search_criteria(DEFAULT_SINCE_DATE, DEFAULT_END_DATE)

    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    end_date_for_name = (
        datetime.utcnow().strftime('%y%m%d')
        if DEFAULT_END_DATE == "Today"
        else datetime.strptime(DEFAULT_END_DATE, '%Y-%m-%d').strftime('%y%m%d')
    )
    mbox_path = DEFAULT_OUT_DIR / (
        f"{IMAP_USERNAME.split('@', 1)[0]}_{datetime.strptime(DEFAULT_SINCE_DATE, '%Y-%m-%d').strftime('%y%m%d')}_{end_date_for_name}.mbox"
    )
    if mbox_path.exists():
        mbox_path.unlink()

    mbox = mailbox.mbox(mbox_path, create=True)

    total_count = 0

    for folder in folders:
        total_count += fetch_folder(imap, folder, search_criteria, mbox)

    mbox.flush()
    mbox.close()
    imap.logout()

    print(f"[+] DONE → {mbox_path}")
    print(f"[+] TOTAL MAILS WRITTEN: {total_count}")

if __name__ == "__main__":
    main()
