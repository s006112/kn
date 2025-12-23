import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, TypeVar

import openai
from perplexity import Perplexity
from google import genai
from dotenv import load_dotenv

from helper.utils_text_processing import _format_text, _normalize_output


load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
STABLY_API_KEY = os.getenv("STABLY_API_KEY")
GROK_API_KEY = os.getenv("GROK_API_KEY")

_OPENAI_CLIENT: Optional[openai.OpenAI] = None
_PPLX_CLIENT: Optional[Any] = None
_GEMINI_CLIENT: Optional[Any] = None
_GROK_CLIENT: Optional[openai.OpenAI] = None



class LLMPermanentFailure(Exception):
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


# client 初始化 -------------------------------------------------------------

def get_openai_client() -> openai.OpenAI:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is missing.")
        _OPENAI_CLIENT = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _OPENAI_CLIENT


def get_perplexity_client() -> Any:
    global _PPLX_CLIENT
    if _PPLX_CLIENT is None:
        if not PERPLEXITY_API_KEY:
            raise RuntimeError("PERPLEXITY_API_KEY is missing.")
        _PPLX_CLIENT = Perplexity(api_key=PERPLEXITY_API_KEY)
    return _PPLX_CLIENT


def get_gemini_client() -> Any:
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing.")
        _GEMINI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
    return _GEMINI_CLIENT


def get_grok_client() -> openai.OpenAI:
    global _GROK_CLIENT
    if _GROK_CLIENT is None:
        if not GROK_API_KEY:
            raise RuntimeError("GROK_API_KEY is missing.")
        _GROK_CLIENT = openai.OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")
    return _GROK_CLIENT


def get_stability_client() -> str:
    """Return Stability API key for image helpers."""
    if not STABLY_API_KEY:
        raise RuntimeError("STABLY_API_KEY is missing.")
    return STABLY_API_KEY

# 共用 messages builder -----------------------------------------------------

T = TypeVar("T")


def _build_messages(
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    role_content_factory: Callable[[str, str], T],
) -> List[T]:
    if messages:
        built: List[T] = []
        for m in messages:
            role = (m.get("role") or "user").strip() or "user"
            text = _format_text(m.get("content"))
            built.append(role_content_factory(role, text))
        if built:
            return built

    built: List[T] = []
    if system_prompt:
        built.append(role_content_factory("system", _format_text(system_prompt)))
    if user_text:
        built.append(role_content_factory("user", _format_text(user_text)))
    if not built:
        built.append(role_content_factory("user", ""))
    return built


# payload builder（OpenAI / Perplexity / Gemini） ---------------------------

def build_openai_payload(system_prompt, user_text, messages):
    def factory(role, text):
        return {"role": role, "content": [{"type": "input_text", "text": text}]}
    return _build_messages(system_prompt, user_text, messages, factory)


def build_perplexity_payload(system_prompt, user_text, messages):
    def factory(role, text):
        return {"role": role, "content": text}
    return _build_messages(system_prompt, user_text, messages, factory)


def build_gemini_payload(system_prompt, user_text, messages):
    def factory(role, text):  # noqa: ARG001 - role unused but keeps signature uniform
        return text
    return "\n\n".join(_build_messages(system_prompt, user_text, messages, factory))


# 文字 backend 呼叫 ---------------------------------------------------------

def call_openai(client: Any, model: str, payload: Any, timeout: int) -> str:
    resp = client.responses.create(model=model, input=payload, timeout=timeout)
    text = getattr(resp, "output_text", None)
    if not text:
        raise RuntimeError("OpenAI returned empty text.")
    return _normalize_output(text)


def _chat_completion_text(resp: Any, provider: str) -> str:
    text = getattr(getattr(getattr(resp, "choices", [None])[0], "message", None), "content", None)
    if not text:
        raise RuntimeError(f"{provider} returned empty text.")
    return _normalize_output(text)


def call_perplexity(client: Any, model: str, payload: Any, timeout: int) -> str:
    resp = client.chat.completions.create(model=model, messages=payload, timeout=timeout)
    return _chat_completion_text(resp, "Perplexity")


def call_grok(client: Any, model: str, payload: Any, timeout: int) -> str:
    resp = client.chat.completions.create(model=model, messages=payload, timeout=timeout)
    return _chat_completion_text(resp, "Grok")


def call_gemini(client: Any, model: str, payload: Any, timeout: int) -> str:
    resp = client.models.generate_content(model=model, contents=payload)
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError(f"Gemini ({model}) returned empty text.")
    return _normalize_output(text)


# backend registry ----------------------------------------------------------

_BACKENDS: Dict[str, Dict[str, Any]] = {
    "perplexity": {
        "match": lambda m: m.lower().startswith("sonar"),
        "client_getter": get_perplexity_client,
        "payload_builder": build_perplexity_payload,
        "call_fn": call_perplexity,
    },
    "gemini": {
        "match": lambda m: m.lower().startswith("gemini"),
        "client_getter": get_gemini_client,
        "payload_builder": build_gemini_payload,
        "call_fn": call_gemini,
    },
    "grok": {
        "match": lambda m: m.lower().startswith("grok-"),
        "client_getter": get_grok_client,
        "payload_builder": build_perplexity_payload,
        "call_fn": call_grok,
    },
    "openai": {
        "match": lambda m: True,
        "client_getter": get_openai_client,
        "payload_builder": build_openai_payload,
        "call_fn": call_openai,
    },
}


def _resolve_backend(model_name: str) -> tuple[str, Dict[str, Any]]:
    for name, cfg in _BACKENDS.items():
        if cfg["match"](model_name):
            return name, cfg
    raise RuntimeError(f"No text backend matched {model_name}")


def _invoke_once(
    model: str,
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    backend_cfg: Dict[str, Any],
    timeout: int,
) -> str:
    client = backend_cfg["client_getter"]()
    payload = backend_cfg["payload_builder"](system_prompt, user_text, messages)
    return backend_cfg["call_fn"](client, model, payload, timeout)


# 統一 LLM 入口 --------------------------------------------------------------

def call_llm(
    model: str,
    *,
    system_prompt: str = "",
    user_text: str = "",
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    file_path: Optional[str] = None,
    max_retries: int = 2,
) -> str:
    if not model:
        raise ValueError("Model name must not be empty.")

    model_name = model.strip()
    backend_name, backend_cfg = _resolve_backend(model_name)

    timeout = 90
    wait = 10
    attempts = max_retries

    for i in range(attempts):
        try:
            return _invoke_once(
                model=model_name,
                system_prompt=system_prompt,
                user_text=user_text,
                messages=messages,
                backend_cfg=backend_cfg,
                timeout=timeout,
            )
        except Exception as exc:
            if i == attempts - 1:
                raise LLMPermanentFailure(
                    f"Model API failed after {attempts} attempts for model {model_name} on file {file_path}: {exc}",
                    model=model_name,
                    backend=backend_name,
                    file_path=file_path,
                    reason=str(exc),
                ) from exc
            time.sleep(wait)
