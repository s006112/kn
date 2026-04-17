import sys
from pathlib import Path

# Allow running from tool/ by adding repo root to sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ali.ali_fetch import fetch_new_messages

# case 1：无 state_store，仅抓 UNSEEN
msgs = fetch_new_messages(max_messages=20)

print("="*50)
print(f"Total fetched: {len(msgs)}")
print("="*50)

for m in msgs:
    print(f"UID: {m.uid}")
    print(f"Message-ID: {m.message_id}")
    print(f"From: {m.from_addr}")
    print(f"To: {m.to_addrs}")
    print(f"Subject: {m.subject}")
    print("Body (first 500 chars):")
    print(m.body_text[:500])
    print("-"*50)
