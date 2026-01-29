#!/usr/bin/env python3
import os
import ssl
import imaplib
import mailbox
from email import message_from_bytes
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# ============================================================
# KEEP YOUR CONFIG (DO NOT TOUCH)
# ============================================================

DEFAULT_SERVER = "mail.ampco.com.hk"
DEFAULT_PORT = 993
DEFAULT_TIMEOUT = 300
DEFAULT_SINCE_DATE = "2026-01-01"
DEFAULT_OUT_DIR = Path("data/mbox/raw")
DEFAULT_STATE_PATH = Path("data/mbox/raw/imap_state.json")
DEFAULT_CHUNK_SIZE = 100

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

def parse_flags_from_line(line):
    # line example:
    # 4757 (UID 887724 FLAGS (\Seen $label2))
    i = line.find("FLAGS (")
    if i == -1:
        return []
    j = line.find(")", i)
    raw = line[i + 7 : j].strip()
    return raw.split()

# ============================================================
# MAIN
# ============================================================

def main():
    # TLS: allow weak DH
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

    imap.select("INBOX")

    since = to_imap_date(DEFAULT_SINCE_DATE)
    typ, data = imap.uid("SEARCH", None, f"SINCE {since}")
    uids = data[0].decode().split()
    print(f"[+] FOUND {len(uids)} mails since {since}")

    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    mbox_path = DEFAULT_OUT_DIR / "test.mbox"
    if mbox_path.exists():
        mbox_path.unlink()

    mbox = mailbox.mbox(mbox_path, create=True)

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

            # tuple: (header, body)
            if isinstance(line, tuple):
                header = line[0]
                body = line[1]
                try:
                    header_text = header.decode(errors="ignore")
                except:
                    header_text = str(header)

                # FLAGS
                if "FLAGS (" in header_text:
                    flags = parse_flags_from_line(header_text)

                # BODY
                if isinstance(body, (bytes, bytearray)):
                    raw_mail = bytes(body)

            else:
                try:
                    text = line.decode(errors="ignore")
                except:
                    text = str(line)

                # sometimes FLAGS only appear in raw line
                if "FLAGS (" in text:
                    flags = parse_flags_from_line(text)

        if raw_mail is None:
            print(f"[!] NO BODY UID {uid}")
            continue

        msg = message_from_bytes(raw_mail)
        m = mailbox.mboxMessage(msg)

        m["X-IMAP-UID"] = uid
        m["X-IMAP-Folder"] = "INBOX"
        m["X-IMAP-Flags"] = " ".join(flags)
        m["X-IMAP-Flags-Count"] = str(len(flags))

        mbox.add(m)

        if any(f.startswith("$label") for f in flags):
            print(f"[HIT] UID {uid} FLAGS {flags}")

    mbox.flush()
    mbox.close()
    imap.logout()

    print(f"[+] DONE → {mbox_path}")

if __name__ == "__main__":
    main()
