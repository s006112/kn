import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import openai
from perplexity import Perplexity
from google import genai
from dotenv import load_dotenv

from utils_text_processing import _format_text, _normalize_output


load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 全局 client 單例
_OPENAI_CLIENT: Optional[openai.OpenAI] = None
_PPLX_CLIENT: Optional[Any] = None
_GEMINI_CLIENT: Optional[genai.Client] = None


class LLMPermanentFailure(Exception):
    """所有重試都失敗時拋出的最終錯誤。"""

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


# client 懶初始化 -----------------------------------------------------------

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


def get_gemini_client() -> genai.Client:
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing.")
        _GEMINI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
    return _GEMINI_CLIENT


# 共用 messages builder -----------------------------------------------------

def _build_messages(
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    role_content_factory: Callable[[str, str], Dict[str, Any]],
) -> List[Dict[str, Any]]:

    if messages:
        built = []
        for m in messages:
            role = (m.get("role") or "user").strip()
            content = _format_text(m.get("content"))
            built.append(role_content_factory(role, content))
        if built:
            return built

    built = []
    if system_prompt:
        built.append(role_content_factory("system", _format_text(system_prompt)))
    if user_text:
        built.append(role_content_factory("user", _format_text(user_text)))
    if not built:
        built.append(role_content_factory("user", ""))
    return built


# payload builder (OpenAI / PPLX / Gemini) ----------------------------------

def build_openai_payload(system_prompt, user_text, messages):
    def factory(role, text):
        return {"role": role, "content": [{"type": "input_text", "text": text}]}
    return _build_messages(system_prompt, user_text, messages, factory)


def build_perplexity_payload(system_prompt, user_text, messages):
    def factory(role, text):
        return {"role": role, "content": text}
    return _build_messages(system_prompt, user_text, messages, factory)


def build_gemini_payload(system_prompt, user_text, messages):
    """
    Gemini text API: 用一個純 text prompt 最穩定。
    """
    parts = []
    if messages:
        for m in messages:
            role = (m.get("role") or "user").strip()
            text = _format_text(m.get("content"))
            if role == "system":
                parts.append(f"[SYSTEM]\n{text}")
            else:
                parts.append(text)
        return "\n\n".join(parts)

    if system_prompt:
        parts.append(f"[SYSTEM]\n{_format_text(system_prompt)}")
    if user_text:
        parts.append(_format_text(user_text))
    return "\n\n".join(parts) if parts else ""


# 各 backend 的統一調用函數 ---------------------------------------------------

def call_openai(client, model, payload, timeout):
    resp = client.responses.create(model=model, input=payload, timeout=timeout)
    text = getattr(resp, "output_text", None)
    if not text:
        raise RuntimeError("OpenAI returned empty text.")
    return _normalize_output(text)


def call_perplexity(client, model, payload, timeout):
    resp = client.chat.completions.create(model=model, messages=payload, timeout=timeout)
    if not resp or not getattr(resp, "choices", None):
        raise RuntimeError("Perplexity returned no content.")
    text = resp.choices[0].message.content
    if not text:
        raise RuntimeError("Perplexity returned empty text.")
    return _normalize_output(text)


def call_gemini(client, model, payload, timeout):
    resp = client.models.generate_content(model=model, contents=payload)
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError("Gemini returned empty text.")
    return _normalize_output(text)


# 圖片專用：Gemini / OpenAI ---------------------------------------------------

def call_openai_image(client, model, prompt, size, n):
    return client.images.generate(model=model, prompt=prompt, size=size, n=n)


def call_gemini_image(client, model, prompt, size, n):
    """
    Gemini image models (如 gemini-2.5-flash-image / gemini-3-pro-image-preview)
    返回 base64 inline_data。
    """
    resp = client.models.generate_content(
        model=model,
        contents=[prompt],
        generation_config={
            "response_mime_type": "image/png",
        },
    )
    # 取出第一張圖片
    for part in resp.candidates[0].content.parts:
        if hasattr(part, "inline_data") and part.inline_data:
            return part.inline_data.data
    raise RuntimeError("Gemini returned no image data.")


# Backend registry (TEXT) ---------------------------------------------------

_BACKENDS = {
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
    "openai": {
        "match": lambda m: True,
        "client_getter": get_openai_client,
        "payload_builder": build_openai_payload,
        "call_fn": call_openai,
    },
}


# Backend registry (IMAGE) --------------------------------------------------

_IMAGE_BACKENDS = {
    "gemini": {
        "match": lambda m: "-image" in m.lower(),   # gemini-2.5-flash-image / gemini-3-pro-image-preview
        "client_getter": get_gemini_client,
        "call_fn": call_gemini_image,
    },
    "openai": {
        "match": lambda m: True,
        "client_getter": get_openai_client,
        "call_fn": call_openai_image,
    },
}


# 後端選擇器 -----------------------------------------------------------------

def _resolve_backend(model_name):
    for name, cfg in _BACKENDS.items():
        if cfg["match"](model_name):
            return name, cfg
    raise RuntimeError(f"No text backend matched {model_name}")


def _resolve_image_backend(model_name):
    for name, cfg in _IMAGE_BACKENDS.items():
        if cfg["match"](model_name):
            return name, cfg
    raise RuntimeError(f"No image backend matched {model_name}")


# 單次調用 -------------------------------------------------------------------

def _invoke_once(
    model, system_prompt, user_text, messages,
    backend_name, backend_cfg, timeout
):
    client = backend_cfg["client_getter"]()
    payload = backend_cfg["payload_builder"](system_prompt, user_text, messages)
    return backend_cfg["call_fn"](client, model, payload, timeout)


# Image wrapper --------------------------------------------------------------

def generate_image(model: str, prompt: str, size="1024x1024", n=1):
    model_name = model.strip()
    backend_name, backend_cfg = _resolve_image_backend(model_name)
    client = backend_cfg["client_getter"]()
    return backend_cfg["call_fn"](client, model_name, prompt, size, n)


# 主入口：call_llm -----------------------------------------------------------

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
    attempts = max_retries + 1

    for i in range(attempts):
        try:
            return _invoke_once(
                model=model_name,
                system_prompt=system_prompt,
                user_text=user_text,
                messages=messages,
                backend_name=backend_name,
                backend_cfg=backend_cfg,
                timeout=timeout,
            )
        except Exception as exc:
            if i == attempts - 1:
                raise LLMPermanentFailure(
                    f"Failed after {attempts} attempts: {exc}",
                    model=model_name,
                    backend=backend_name,
                    file_path=file_path,
                    reason=str(exc),
                ) from exc
            time.sleep(10)
