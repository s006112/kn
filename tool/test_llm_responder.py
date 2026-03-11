import sys
from pathlib import Path

# Allow running from tool/ by adding repo root to sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ali import LLM_MODEL, SYSTEM_PROMPT_PATH
from ali.ali_fetch import fetch_new_messages
from ali.ali_llm import generate_review_package, render_review

msgs = fetch_new_messages(max_messages=1)
if not msgs:
    print("No messages.")
else:
    review = generate_review_package(
        msgs[0],
        system_prompt_path=SYSTEM_PROMPT_PATH,
        model=LLM_MODEL,
    )
    reply = render_review(review)
    print("====== REPLY BODY ======")
    print(reply)
