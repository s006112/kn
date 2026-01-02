"""
SYSTEM INVARIANTS (NON-NEGOTIABLE)

1. No Autonomous Action
   The system MUST NOT send any message to customers or third parties autonomously.
   All generated content is internal-only unless a human explicitly copies and sends it.

2. Silence Means Termination
   If the engineer does not reply with any non-empty content,
   the system MUST treat the review as rejected and MUST NOT continue processing.

3. Any Reply Is an Override
   Any non-empty reply from the engineer MUST be interpreted as override instructions
   and MUST trigger a regenerated internal review using that reply as hard constraints.
"""


from __future__ import annotations

import time
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from helper.utils_config import configure_logging, get_env_int  # type: ignore
from ali_fetch import fetch_new_messages  # type: ignore
from ali_llm import generate_review_package, render_review  # type: ignore
from ali_send import send_reply  # type: ignore
from helper.utils_imap_types import EmailMessage, SendResult

# sonar, sonar-pro, sonar-reasoning, sonar-reasoning-pro
# gemini-2.0-flash, gemini-2.5-flash, gemini-2.5-pro, gemini-3-pro-preview, 
# gpt-5-mini, gpt-5-nano, gpt-4.1-mini, gpt-4.1-nano, gpt-4o-mini, o1-mini, o3-mini, o4-mini, codex-mini-latest
# gpt-5.1, gpt-5, gpt-5-chat-latest, gpt-4.1, gpt-4o, o1, o3,

LLM_MODEL = "gpt-4.1-mini"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompt" / "prompt_ali_system.txt"

_HKT_ZONE = ZoneInfo("Asia/Hong_Kong")
_DAY_START = dt_time(9, 0)
_DAY_END = dt_time(18, 0)


def _default_poll_interval_minutes(now: datetime | None = None) -> int:
    current = now or datetime.now(tz=_HKT_ZONE)
    local_time = current.timetz().replace(tzinfo=None)
    return 1 if _DAY_START <= local_time < _DAY_END else 10


def pipeline_run() -> None:
    logger = configure_logging("ali_pipeline")

    messages: list[EmailMessage] = fetch_new_messages(max_messages=2)
    if not messages:
        logger.info("No new messages to process.")
        return

    for msg in messages:
        try:
            logger.info("Processing uid=%s subject=%s", msg.uid, msg.subject)

            # 1) 生成 INTERNAL review 内容（这是 reply_body）
            review_obj = generate_review_package(
                msg,
                system_prompt_path=SYSTEM_PROMPT_PATH,
                model=LLM_MODEL,
            )
            reply_body = render_review(review_obj)

            # 2) reviewer 永远是 forward 的人
            reviewer = msg.from_addr
            if not reviewer:
                raise RuntimeError("Missing reviewer (msg.from_addr is empty)")

            # 3) 构造 review_msg：
            #    - body_text = 原始客户内容（用于 quoted）
            #    - reply_body = review 内容（只出现一次）
            review_msg = EmailMessage(
                uid=msg.uid,
                message_id=msg.message_id,
                from_addr=reviewer,
                to_addrs=[reviewer],
                cc_addrs=[],
                subject=f"[ALI REVIEW] {msg.subject}",
                body_text=msg.body_text,   # ⚠️ 原始客户内容
                raw_bytes=msg.raw_bytes,
            )

            # 4) 发送 review（send_reply 会自动把 body_text quote 在下面）
            result: SendResult = send_reply(review_msg, reply_body)

            if result.ok:
                logger.info("Review sent to %s (uid=%s)", reviewer, msg.uid)
            else:
                logger.error(
                    "Send failed for uid=%s: %s",
                    msg.uid,
                    result.error_message or "unknown error",
                )

        except Exception as exc:
            logger.error("Unhandled error processing uid=%s: %s", msg.uid, exc)


    logger.info("Pipeline run finished.")


if __name__ == "__main__":
    while True:
        pipeline_run()
        interval_minutes = get_env_int(
            "ALI_POLL_INTERVAL_MINUTES",
            _default_poll_interval_minutes(),
        )
        time.sleep(interval_minutes * 60)
