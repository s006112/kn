#!/usr/bin/env python3
"""
test_full_ali_pipeline.py

此腳本會進行完整 end-to-end 測試：

1. 從 IMAP 抓取未讀郵件（ali_email_fetcher）
2. 對每封郵件呼叫 LLM 生成回覆（ali_llm_responder）
3. 立即寄出回覆給原寄件人（ali_email_sender）

請確認：
- .env 中 IMAP_* 與 SMTP_* 已正確設定
- OPENAI_API_KEY 已設定（或你的 LLM backend）
"""

from ali_email_fetcher import fetch_new_messages
from ali_llm_responder import generate_reply
from ali_email_sender import send_reply


def main():
    print("=== Fetching new emails ===")
    messages = fetch_new_messages(max_messages=5)  # 可調整
    print(f"Total fetched: {len(messages)}")

    if not messages:
        print("No UNSEEN messages. End.")
        return

    for msg in messages:
        print("--------------------------------------------------")
        print(f"UID: {msg.uid}")
        print(f"From: {msg.from_addr}")
        print(f"Subject: {msg.subject}")
        print("Body preview:", msg.body_text[:200].replace("\n", " "))
        print("--------------------------------------------------")

        print("=== Generating LLM reply... ===")
        try:
            reply_body = generate_reply(msg)
            print("LLM reply preview:", reply_body[:200].replace("\n", " "))
        except Exception as e:
            print("LLM ERROR:", e)
            continue

        print("=== Sending reply email... ===")
        result = send_reply(msg, reply_body)

        if result.ok:
            print(f"[OK] Reply sent to {msg.from_addr}")
        else:
            print(f"[FAIL] Could not send reply for UID {msg.uid}: {result.error_message}")


if __name__ == "__main__":
    main()
