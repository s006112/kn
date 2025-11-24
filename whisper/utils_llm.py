import os
import logging
import time
import re
import openai
from dotenv import load_dotenv

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

try:
    from perplexity import Perplexity
except ImportError:  # pragma: no cover - optional dependency
    Perplexity = None

_PERPLEXITY_CLIENT = None


class OpenAIPermanentFailure(Exception):
    """Raised when OpenAI API fails after all retries."""

    def __init__(self, message, model=None, file_path=None, reason=None):
        super().__init__(message)
        self.model = model
        self.file_path = file_path
        self.reason = reason


def create_openai_client():
    """Create an OpenAI client using the global OPENAI_API_KEY."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
    return openai.OpenAI(api_key=OPENAI_API_KEY)


def process_text_with_openai(
    client, model, system_prompt, text, file_path=None, diagnostics=False
):
    """
    Unified helper for OpenAI / Perplexity text processing with retry logic.
    Behavior matches the legacy extraction helper used in earlier pipelines.
    """
    model_name = (model or "").strip()
    timeout = 90 if model_name.startswith("gpt-5") else 30
    wait = 10
    max_wait = 30 if timeout == 90 else 15
    use_perplexity = model_name.lower().startswith("sonar")

    for attempt in range(2):
        try:
            if use_perplexity:
                return _process_text_with_perplexity(
                    model_name or "sonar", system_prompt, text
                )
            payload = _build_openai_payload(system_prompt, text)
            return client.responses.create(
                model=model_name, input=payload, timeout=timeout
            ).output_text
        except Exception as exc:
            if attempt == 1:
                raise OpenAIPermanentFailure(
                    f"Model API failed after 2 attempts for model {model_name} on file {file_path}: {exc}",
                    model=model_name,
                    file_path=file_path,
                    reason=str(exc),
                )
            wait = min(wait * 2, max_wait)
            time.sleep(wait)


def _build_openai_payload(system_prompt, text):
    def block(role, value):
        return {
            "role": role,
            "content": [
                {"type": "input_text", "text": (value or "").strip()},
            ],
        }

    payload = []
    if system_prompt:
        payload.append(block("system", system_prompt))
    if text:
        payload.append(block("user", text))
    if not payload:
        payload.append(block("user", ""))
    return payload


def _process_text_with_perplexity(model_name, system_prompt, text):
    client = _get_perplexity_client()
    messages = _build_perplexity_messages(system_prompt, text)
    response = client.chat.completions.create(model=model_name, messages=messages)
    if not response or not getattr(response, "choices", None):
        raise RuntimeError("Perplexity returned no content")
    message = response.choices[0].message
    content = getattr(message, "content", "")
    text_output = _extract_perplexity_content(content)
    if not text_output:
        raise RuntimeError("Perplexity response content was empty")
    return text_output


def _build_perplexity_messages(system_prompt, text):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt.strip()})
    if text:
        messages.append({"role": "user", "content": text.strip()})
    if not messages:
        messages.append({"role": "user", "content": ""})
    return messages


def _extract_perplexity_content(content):
    if isinstance(content, str):
        text = content.strip()
    elif not content:
        return ""
    else:
        parts = []
        for chunk in content:
            chunk_text = getattr(chunk, "text", None)
            if chunk_text is None and isinstance(chunk, dict):
                chunk_text = chunk.get("text")
            if chunk_text:
                parts.append(chunk_text)
        text = "\n".join(parts).strip()
    return _strip_think_segments(text)


def _strip_think_segments(text):
    if not text or "<" not in text:
        return text

    tag_pattern = re.compile(r"<\s*(/?)\s*think\b[^>]*>", re.IGNORECASE)
    depth = 0
    last_index = 0
    output = []

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


def _get_perplexity_client():
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
