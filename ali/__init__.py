"""
ali package defaults.

职责：
- 暴露 ALI tool scripts 共用的默认 model 和 system prompt path。

Used by:
- tool/test_llm_responder.py
- tool/test_email_sender.py
"""

from pathlib import Path

LLM_MODEL = "sonar-pro"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompt" / "prompt_ali_system.txt"
