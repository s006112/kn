import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional

import openai
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

try:
    from perplexity import Perplexity
except ImportError:  # pragma: no cover - optional dependency
    Perplexity = None

_OPENAI_CLIENT: Optional[openai.OpenAI] = None
_PERPLEXITY_CLIENT: Optional[Any] = None


class OpenAIPermanentFailure(Exception):
    """Raised when text model APIs fail after all retries."""

    def __init__(
        self,
        message: str,
        model: Optional[str] = None,
        file_path: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.model = model
        self.file_path = file_path
        self.reason = reason


def _get_openai_client() -> openai.OpenAI:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        _OPENAI_CLIENT = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _OPENAI_CLIENT


def _get_perplexity_client() -> Any:
    global _PERPLEXITY_CLIENT
    if _PERPLEXITY_CLIENT is None:
        if Perplexity is None:
            raise RuntimeError(
                "Perplexity SDK is not installed. Run `pip install perplexityai`."
            )
        if not PERPLEXITY_API_KEY:
            raise RuntimeError("PERPLEXITY_API_KEY environment variable is not set.")
        _PERPLEXITY_CLIENT = Perplexity(api_key=PERPLEXITY_API_KEY)
    return _PERPLEXITY_CLIENT


def _strip_think_segments(text: str) -> str:
    if not text or "<" not in text:
        return text

    tag_pattern = re.compile(r"<\s*(/?)\s*think\b[^>]*>", re.IGNORECASE)
    depth = 0
    last_index = 0
    output: List[str] = []

    for match in tag_pattern.finditer(text):
        start, end = match.span()
        is_closing = bool(match.group(1))

        if depth == 0:
            output.append(text[last_index:start])

        if not is_closing:
            if depth == 0:
                last_index = end
            depth += 1
        else:
            if depth > 0:
                depth -= 1
                if depth == 0:
                    last_index = end
            else:
                output.append(match.group(0))
                last_index = end

    if depth == 0:
        output.append(text[last_index:])

    return "".join(output)


def _format_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _build_openai_payload(
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    def block(role: str, value: str) -> Dict[str, Any]:
        return {
            "role": role,
            "content": [{"type": "input_text", "text": value}],
        }

    payload: List[Dict[str, Any]] = []
    if messages:
        for message in messages:
            role = (message.get("role") or "user").strip() or "user"
            content = _format_text(message.get("content"))
            payload.append(block(role, content))
    else:
        if system_prompt:
            payload.append(block("system", _format_text(system_prompt)))
        if user_text:
            payload.append(block("user", _format_text(user_text)))
    if not payload:
        payload.append(block("user", ""))
    return payload


def _build_perplexity_messages(
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    if messages:
        built = []
        for message in messages:
            role = (message.get("role") or "user").strip() or "user"
            built.append({"role": role, "content": _format_text(message.get("content"))})
        return built or [{"role": "user", "content": ""}]

    built: List[Dict[str, str]] = []
    if system_prompt:
        built.append({"role": "system", "content": _format_text(system_prompt)})
    if user_text:
        built.append({"role": "user", "content": _format_text(user_text)})
    if not built:
        built.append({"role": "user", "content": ""})
    return built


def _call_openai_text(
    model: str,
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    timeout: int,
) -> str:
    client = _get_openai_client()
    payload = _build_openai_payload(system_prompt, user_text, messages)
    response = client.responses.create(model=model, input=payload, timeout=timeout)
    output = getattr(response, "output_text", None)
    if not isinstance(output, str):
        raise RuntimeError("OpenAI response did not contain text.")
    return _strip_think_segments(output)


def _call_perplexity_text(
    model: str,
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
) -> str:
    client = _get_perplexity_client()
    msg_payload = _build_perplexity_messages(system_prompt, user_text, messages)
    response = client.chat.completions.create(model=model, messages=msg_payload)
    if not response or not getattr(response, "choices", None):
        raise RuntimeError("Perplexity returned no content")
    message = response.choices[0].message
    content = getattr(message, "content", "")
    text_output = _extract_perplexity_content(content)
    if not text_output:
        raise RuntimeError("Perplexity response content was empty")
    return text_output


def _extract_perplexity_content(content: Any) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif not content:
        return ""
    else:
        parts: List[str] = []
        for chunk in content:
            chunk_text = getattr(chunk, "text", None)
            if chunk_text is None and isinstance(chunk, dict):
                chunk_text = chunk.get("text")
            if chunk_text:
                parts.append(str(chunk_text))
        text = "\n".join(parts).strip()
    return _strip_think_segments(text)


def call_llm(
    model: str,
    *,
    system_prompt: str = "",
    user_text: str = "",
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    file_path: Optional[str] = None,
    max_retries: int = 2,
) -> str:
    """
    Unified helper for OpenAI responses API and Perplexity chat completions.
    sonar* models automatically route to Perplexity, others go to OpenAI.
    """
    model_name = (model or "").strip()
    if not model_name:
        raise ValueError("Model name must not be empty.")

    use_perplexity = model_name.lower().startswith("sonar")
    timeout = 90 if model_name.startswith("gpt-5") else 30
    wait = 10
    max_wait = 30 if timeout == 90 else 15
    attempts = max(1, max_retries)

    for attempt in range(attempts):
        try:
            if use_perplexity:
                return _call_perplexity_text(
                    model_name,
                    system_prompt,
                    user_text,
                    messages,
                )
            return _call_openai_text(
                model_name,
                system_prompt,
                user_text,
                messages,
                timeout,
            )
        except Exception as exc:
            if attempt == attempts - 1:
                raise OpenAIPermanentFailure(
                    f"Model API failed after {attempts} attempts for model {model_name} on file {file_path}: {exc}",
                    model=model_name,
                    file_path=file_path,
                    reason=str(exc),
                )
            wait = min(wait * 2, max_wait)
            time.sleep(wait)


def get_openai_client() -> openai.OpenAI:
    """Expose the shared OpenAI client for image or other APIs."""
    return _get_openai_client()


__all__ = ["OpenAIPermanentFailure", "call_llm", "get_openai_client"]
