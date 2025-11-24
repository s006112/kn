import os
import re
import time
import inspect
from typing import Any, Callable, Dict, Iterable, List, Optional

import openai
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

try:
    from perplexity import Perplexity
except ImportError:  # optional dependency
    Perplexity = None

_PRIMARY_CLIENT: Optional[openai.OpenAI] = None
_PPLX_CLIENT: Optional[Any] = None


class LLMPermanentFailure(Exception):
    """Raised when any LLM backend fails after retries."""

    def __init__(
        self,
        message: str,
        model: Optional[str] = None,
        backend: Optional[str] = None,
        file_path: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.model = model
        self.backend = backend
        self.file_path = file_path
        self.reason = reason


def _get_primary_client() -> openai.OpenAI:
    global _PRIMARY_CLIENT
    if _PRIMARY_CLIENT is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        _PRIMARY_CLIENT = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _PRIMARY_CLIENT


def _get_pplx_client() -> Any:
    global _PPLX_CLIENT
    if _PPLX_CLIENT is None:
        if Perplexity is None:
            raise RuntimeError("Perplexity SDK not installed. Run `pip install perplexityai`.")
        if not PERPLEXITY_API_KEY:
            raise RuntimeError("PERPLEXITY_API_KEY environment variable is not set.")
        _PPLX_CLIENT = Perplexity(api_key=PERPLEXITY_API_KEY)
    return _PPLX_CLIENT


def _format_text(v: Any) -> str:
    if v is None:
        return ""
    return v.strip() if isinstance(v, str) else str(v).strip()


_THINK_TAG = re.compile(r"<\s*(/?)\s*think\b[^>]*>", re.IGNORECASE)


def _strip_think(text: str) -> str:
    if not text or "<" not in text:
        return text
    depth, last, out = 0, 0, []
    for m in _THINK_TAG.finditer(text):
        s, e = m.span()
        closing = bool(m.group(1))
        if depth == 0:
            out.append(text[last:s])
        if not closing:
            if depth == 0:
                last = e
            depth += 1
        else:
            if depth > 0:
                depth -= 1
                if depth == 0:
                    last = e
            else:
                out.append(m.group(0))
                last = e
    if depth == 0:
        out.append(text[last:])
    return "".join(out)


def _normalize_output(content: Any) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif not content:
        text = ""
    else:
        parts: List[str] = []
        for c in content:
            t = getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else None)
            if t:
                parts.append(str(t))
        text = "\n".join(parts).strip()
    return _strip_think(text)


def _build_responses_payload(
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    def block(role: str, value: str) -> Dict[str, Any]:
        return {"role": role, "content": [{"type": "input_text", "text": value}]}

    if messages:
        return [
            block((m.get("role") or "user").strip() or "user", _format_text(m.get("content")))
            for m in messages
        ] or [block("user", "")]

    payload: List[Dict[str, Any]] = []
    if system_prompt:
        payload.append(block("system", _format_text(system_prompt)))
    if user_text:
        payload.append(block("user", _format_text(user_text)))
    return payload or [block("user", "")]


def _build_chat_messages(
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    if messages:
        built = [
            {
                "role": (m.get("role") or "user").strip() or "user",
                "content": _format_text(m.get("content")),
            }
            for m in messages
        ]
        return built or [{"role": "user", "content": ""}]

    built: List[Dict[str, str]] = []
    if system_prompt:
        built.append({"role": "system", "content": _format_text(system_prompt)})
    if user_text:
        built.append({"role": "user", "content": _format_text(user_text)})
    return built or [{"role": "user", "content": ""}]


def _invoke_openai(
    model: str,
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    timeout: int,
) -> str:
    client = _get_primary_client()
    payload = _build_responses_payload(system_prompt, user_text, messages)
    resp = client.responses.create(model=model, input=payload, timeout=timeout)
    out = getattr(resp, "output_text", None)
    if not isinstance(out, str):
        raise RuntimeError("Primary backend response did not contain text.")
    return _normalize_output(out)


def _invoke_pplx(
    model: str,
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    timeout: int,
) -> str:
    client = _get_pplx_client()
    msg_payload = _build_chat_messages(system_prompt, user_text, messages)

    # SDK 可能不支持 timeout；若 TypeError 则降级重试一次
    try:
        resp = client.chat.completions.create(model=model, messages=msg_payload, timeout=timeout)
    except TypeError:
        resp = client.chat.completions.create(model=model, messages=msg_payload)

    if not resp or not getattr(resp, "choices", None):
        raise RuntimeError("Perplexity returned no content.")
    content = getattr(resp.choices[0].message, "content", "")
    text = _normalize_output(content)
    if not text:
        raise RuntimeError("Perplexity response content was empty.")
    return text

# Backend registry: add new backends here only.
_BACKENDS: Dict[str, Dict[str, Any]] = {
    "perplexity": {
        "match": lambda name: name.lower().startswith("sonar"),
        "invoke": _invoke_pplx,
    },
    "openai": {
        "match": lambda name: True,  # default fallback
        "invoke": _invoke_openai,
    },
}


def _resolve_backend(model_name: str) -> str:
    for name, cfg in _BACKENDS.items():
        try:
            if cfg["match"](model_name):
                return name
        except Exception:
            continue
    return "openai"


def call_llm(
    model: str,
    *,
    system_prompt: str = "",
    user_text: str = "",
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    file_path: Optional[str] = None,
    max_retries: int = 2,
) -> str:
    """Unified LLM entry: route -> invoke -> normalize -> retry."""
    if not model:
        raise ValueError("Model name must not be empty.")
    model_name = model.strip()

    invoke = _BACKENDS[_resolve_backend(model_name)]["invoke"]
    timeout = 90

    wait = 10  # fixed backoff

    for i in range(max_retries):
        try:
            return invoke(model_name, system_prompt, user_text, messages, timeout)
        except Exception as exc:
            if i == max_retries - 1:
                raise LLMPermanentFailure(
                    f"Model API failed after {max_retries} attempts for model {model_name} on file {file_path}: {exc}",
                    model=model_name,
                    backend=_resolve_backend(model_name),
                    file_path=file_path,
                    reason=str(exc),
                )
            time.sleep(wait)


# Backward-compatible export name
def get_openai_client() -> openai.OpenAI:
    """Expose the primary backend client (currently OpenAI)."""
    return _get_primary_client()


__all__ = ["LLMPermanentFailure", "call_llm", "get_openai_client"]
