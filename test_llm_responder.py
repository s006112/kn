from ali_fetch import fetch_new_messages
from ali_llm import generate_reply
from ali_email import LLM_MODEL, SYSTEM_PROMPT_PATH

msgs = fetch_new_messages(max_messages=1)
if not msgs:
    print("No messages.")
else:
    reply = generate_reply(
        msgs[0],
        system_prompt_path=SYSTEM_PROMPT_PATH,
        model=LLM_MODEL,
    )
    print("====== REPLY BODY ======")
    print(reply)
