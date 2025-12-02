from ali_fetch import fetch_new_messages

# case 1：无 state_store，仅抓 UNSEEN
msgs = fetch_new_messages(state=None, max_messages=5)

print("="*50)
print(f"Total fetched: {len(msgs)}")
print("="*50)

for m in msgs:
    print(f"UID: {m.uid}")
    print(f"Message-ID: {m.message_id}")
    print(f"From: {m.from_addr}")
    print(f"To: {m.to_addrs}")
    print(f"Subject: {m.subject}")
    print("Body (first 200 chars):")
    print(m.body_text[:200])
    print("-"*50)
