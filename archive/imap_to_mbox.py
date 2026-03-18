#!/usr/bin/env python3
"""
imap_to_mbox.py

Responsibility:
Fetch messages from selected IMAP folders since a fixed date and write them into a local mbox file, annotating each
message with IMAP-derived metadata headers.

Pipelines:
- env_load -> connect -> login -> select -> search -> fetch -> parse -> annotate -> mbox_write -> logout

Invariants:
- Reads IMAP credentials from environment at import time and raises if missing.
- Fetches message bodies using `BODY.PEEK[]` and does not set message `\\Seen` as part of the fetch call.
- Overwrites `data/mbox/raw/test.mbox` on each run.

Out of scope:
- Incremental state tracking or resume support.
- Server-side changes (marking, moving, deleting).
- Robust folder discovery; folder names are hard-coded.
"""

import os
import ssl
import imaplib
import mailbox
from email import message_from_bytes
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# Configuration lives in module constants so runs are reproducible without requiring CLI arguments.

DEFAULT_SERVER = "mail.ampco.com.hk"
DEFAULT_PORT = 993
DEFAULT_TIMEOUT = 300
DEFAULT_SINCE_DATE = "2025-12-01"
DEFAULT_OUT_DIR = Path("data/mbox/raw")
DEFAULT_STATE_PATH = Path("data/mbox/raw/imap_state.json")
DEFAULT_CHUNK_SIZE = 100

# Credentials are loaded from `.env` to avoid embedding secrets in the repo.

load_dotenv()
IMAP_USERNAME = os.getenv("IMAP_USERNAME")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")
IMAP_SERVER = os.getenv("IMAP_SERVER", DEFAULT_SERVER)
IMAP_PORT = int(os.getenv("IMAP_PORT", DEFAULT_PORT))

if not IMAP_USERNAME or not IMAP_PASSWORD:
    raise RuntimeError("IMAP_USERNAME / IMAP_PASSWORD not found in .env")

def to_imap_date(d):
    """
    Purpose:
    Convert a YYYY-MM-DD date string into an IMAP SEARCH-compatible date string.

    Inputs:
    - d: Date string in %Y-%m-%d format.

    Outputs:
    - IMAP date string in %d-%b-%Y format.
    """
    return datetime.strptime(d, "%Y-%m-%d").strftime("%d-%b-%Y")

def parse_flags_from_line(line):
    """
    Purpose:
    Extract IMAP flags from a FETCH response header line containing a FLAGS list.

    Inputs:
    - line: Text line that may contain a substring like "FLAGS ( ... )".

    Outputs:
    - List of flag tokens (strings). Returns an empty list if no FLAGS section is present.
    """
    i = line.find("FLAGS (")
    if i == -1:
        return []
    j = line.find(")", i)
    raw = line[i + 7 : j].strip()
    return raw.split()

def fetch_folder(imap, folder, since, mbox):
    """
    Purpose:
    Search an IMAP folder for messages since a given date and append each message to an mbox mailbox file.

    Inputs:
    - imap: Connected and logged-in IMAP4_SSL client.
    - folder: Folder name to select (e.g., "INBOX", "SENT").
    - since: IMAP date string in %d-%b-%Y format used in the SEARCH SINCE query.
    - mbox: Open `mailbox.mbox` instance to append messages into.

    Outputs:
    - None. Appends messages to `mbox` and prints progress and label hits to stdout.
    """
    imap.select(folder)
    typ, data = imap.uid("SEARCH", None, f"SINCE {since}")
    uids = data[0].decode().split()
    print(f"[+] FOUND {len(uids)} mails in {folder} since {since}")

    for uid in uids:
        typ, resp = imap.uid("FETCH", uid, "(UID FLAGS BODY.PEEK[])")
        if typ != "OK":
            print(f"[!] FETCH FAIL UID {uid}")
            continue

        flags = []
        raw_mail = None

        for line in resp:
            if not line:
                continue

            if isinstance(line, tuple):
                header = line[0]
                body = line[1]
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
            print(f"[!] NO BODY UID {uid}")
            continue

        msg = message_from_bytes(raw_mail)
        m = mailbox.mboxMessage(msg)

        m["X-IMAP-UID"] = uid
        m["X-IMAP-Folder"] = folder
        m["X-IMAP-Flags"] = " ".join(flags)
        m["X-IMAP-Flags-Count"] = str(len(flags))

        mbox.add(m)

        if any(f.startswith("$label") for f in flags):
            print(f"[HIT] UID {uid} FLAGS {flags}")

def main():
    """
    Purpose:
    Connect to the configured IMAP server, export messages from fixed folders to an mbox file, and close the session.

    Inputs:
    - None.

    Outputs:
    - None. Writes `data/mbox/raw/test.mbox` and prints status to stdout.
    """
    # Some servers require legacy cipher suites during TLS negotiation.
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

    since = to_imap_date(DEFAULT_SINCE_DATE)

    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    mbox_path = DEFAULT_OUT_DIR / "test.mbox"
    if mbox_path.exists():
        mbox_path.unlink()

    mbox = mailbox.mbox(mbox_path, create=True)

    for folder in ("SENT", "INBOX"):
        fetch_folder(imap, folder, since, mbox)

    mbox.flush()
    mbox.close()
    imap.logout()

    print(f"[+] DONE → {mbox_path}")

if __name__ == "__main__":
    main()
