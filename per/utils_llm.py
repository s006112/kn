from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Sequence

from openai import OpenAI

_CLIENT: Optional[OpenAI] = None


def get_llm_client(api_key_env: str = "OPENAI_API_KEY") -> OpenAI:
    """Return a cached LLM client (currently OpenAI) using the given API key env var."""
    global _CLIENT
    if _CLIENT is None:
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(f"{api_key_env} is not configured.")
        _CLIENT = OpenAI(api_key=api_key)
    return _CLIENT


def llm_call(
    messages: Sequence[Mapping[str, Any]],
    *,
    model: str,
    temperature: Optional[float] = None,
) -> str:
    """Low-level helper to call a chat-style LLM with an explicit messages list."""
    client = get_llm_client()
    kwargs: dict[str, Any] = {"model": model, "messages": list(messages)}
    if temperature is not None:
        kwargs["temperature"] = temperature
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def run_prompt(
    prompt: str,
    body: str,
    *,
    model: str,
    placeholder: str = "{context}",
    multi_message: bool = False,
    temperature: Optional[float] = None,
) -> str:
    """
    Convenience wrapper for the two main patterns:
      - single prompt + body (default)
      - multi_message=True: first body, then prompt as separate messages.
    """
    if multi_message:
        messages: list[Mapping[str, Any]] = [
            {"role": "user", "content": body},
            {"role": "user", "content": prompt},
        ]
    else:
        if placeholder and placeholder in prompt:
            content = prompt.replace(placeholder, body)
        else:
            content = f"{prompt}\n\n{body}"
        messages = [{"role": "user", "content": content}]
    return llm_call(messages, model=model, temperature=temperature)


__all__ = [
    "get_llm_client",
    "llm_call",
    "run_prompt",
]

