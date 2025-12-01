from ali_email_fetcher import fetch_new_messages
from ali_llm_responder import generate_reply

msgs = fetch_new_messages(max_messages=1)
if not msgs:
    print("No messages.")
else:
    reply = generate_reply(msgs[0])
    print("====== REPLY BODY ======")
    print(reply)
