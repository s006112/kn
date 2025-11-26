import os
import time
import base64
from typing import Any, Callable, Dict, Iterable, List, Optional, TypeVar

import requests
import openai
from perplexity import Perplexity
from google import genai
from google.genai import errors as genai_errors
from dotenv import load_dotenv

from utils_text_processing import _format_text, _normalize_output


load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
STABLY_API_KEY = os.getenv("STABLY_API_KEY") 

_OPENAI_CLIENT: Optional[openai.OpenAI] = None
_PPLX_CLIENT: Optional[Any] = None
_GEMINI_CLIENT: Optional[Any] = None



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

def get_stability_client() -> str:
    """
    Stability Ultra 不需要 stateful client，只要 API key。
    """
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


def call_perplexity(client: Any, model: str, payload: Any, timeout: int) -> str:
    resp = client.chat.completions.create(model=model, messages=payload, timeout=timeout)
    text = getattr(getattr(getattr(resp, "choices", [None])[0], "message", None), "content", None)
    if not text:
        raise RuntimeError("Perplexity returned empty text.")
    return _normalize_output(text)


def call_gemini(client: Any, model: str, payload: Any, timeout: int) -> str:
    resp = client.models.generate_content(model=model, contents=payload)
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError(f"Gemini ({model}) returned empty text.")
    return _normalize_output(text)




# 圖像 backend 呼叫：統一回傳 List[bytes] -----------------------------------

def call_stability_image(
    api_key: str,
    model: str,
    prompt: str,
    size: str,
    n: int,
    init_image: Optional[bytes] = None,
    image_strength: float = 0.45,  # 建議先用 0.3–0.6，而不是 1.0
) -> List[bytes]:
    """
    Stability.ai Stable Image v2beta:
    - text-to-image: 只需要 prompt（可選 aspect_ratio 等）
    - image-to-image: 必須提供 image、strength、mode="image-to-image"
      strength ∈ [0,1]，越小越接近原圖，越大越接近純文本生圖。
    """
    if not api_key:
        raise RuntimeError("STABLY_API_KEY is missing.")

    # 1) 根據自訂 model 名決定 endpoint
    model_lower = model.lower().strip()
    if model_lower == "stability-ultra":
        endpoint = "ultra"
    elif model_lower == "stability-core":
        endpoint = "core"
    elif model_lower == "stability-sd3":
        endpoint = "sd3"
    else:
        raise RuntimeError(f"Unknown Stability model alias: {model}")

    url = f"https://api.stability.ai/v2beta/stable-image/generate/{endpoint}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "image/*",
    }

    files: Dict[str, Any] = {
        "prompt": (None, prompt),
        "output_format": (None, "png"),
    }

    if init_image is not None:
        # 真正的 v2beta image-to-image 參數
        strength = max(0.0, min(1.0, image_strength))
        files["mode"] = (None, "image-to-image")
        files["image"] = ("init.png", init_image, "image/png")
        files["strength"] = (None, str(strength))
        print(
            "Stability image-to-image mode,"
            f" init_image size = {len(init_image)}, strength = {strength}"
        )
    else:
        # 純文字生圖
        files["mode"] = (None, "text-to-image")
        print("Stability text-to-image mode")

    images: List[bytes] = []
    count = max(1, n)

    for _ in range(count):
        resp = requests.post(url, headers=headers, files=files, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Stability image API error {resp.status_code}: {resp.text[:200]}"
            )
        images.append(resp.content)

    return images


def call_openai_image(
    client: openai.OpenAI,
    model: str,
    prompt: str,
    size: str,
    n: int,
) -> List[bytes]:
    """
    OpenAI images.generate：
    - 不傳 response_format（避免 400 unknown_parameter）
    - 如果有 b64_json 就 decode
    - 否則如果有 url 就自己 requests 拿 bytes
    """
    resp = client.images.generate(model=model, prompt=prompt, size=size, n=n)

    data = getattr(resp, "data", None) or []
    if not data:
        raise RuntimeError("OpenAI image API returned no data.")

    def _extract_bytes(item: Any) -> Optional[bytes]:
        b64 = getattr(item, "b64_json", None)
        if b64:
            try:
                return base64.b64decode(b64)
            except Exception as exc:
                raise RuntimeError("OpenAI image payload is not valid base64.") from exc

        url = getattr(item, "url", None)
        if not url:
            return None
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        return response.content

    images = [img for item in data if (img := _extract_bytes(item))]
    if not images:
        raise RuntimeError("OpenAI image API did not return any decodable image.")
    return images


def call_gemini_image(
    client: Any,
    model: str,
    prompt: str,
    size: str,
    n: int,
) -> List[bytes]:
    """
    Gemini image models（如 gemini-2.5-flash-image / gemini-3-pro-image-preview）：
    - 不傳 generation_config，兼容較舊 SDK
    - 從 candidates[].content.parts[].inline_data.data 或 file_data 取圖
    """
    def _ensure_bytes(data: Any) -> bytes:
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        if isinstance(data, memoryview):
            return data.tobytes()
        if isinstance(data, str):
            try:
                return base64.b64decode(data)
            except Exception:
                return data.encode("utf-8")
        return bytes(data)

    def _download(uri: str) -> bytes:
        files_client = getattr(client, "files", None)
        if files_client and hasattr(files_client, "download"):
            return files_client.download(file=uri)
        resp = requests.get(uri, timeout=60)
        resp.raise_for_status()
        return resp.content

    def _iter_images(response: Any):
        generated = getattr(response, "generated_images", None) or []
        for item in generated:
            image = getattr(item, "image", None)
            data = getattr(image, "image_bytes", None) if image else None
            if data:
                yield _ensure_bytes(data)

        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None) or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline:
                    data = getattr(inline, "data", None)
                    if data:
                        yield _ensure_bytes(data)
                        continue
                file_data = getattr(part, "file_data", None)
                if file_data:
                    uri = getattr(file_data, "file_uri", None)
                    if uri:
                        yield _download(uri)

    try:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,  # ← 正確：圖片模型接受純文字 prompt
        )
    except genai_errors.ClientError as exc:
        # 更友善顯示配額 / 流量限制錯誤
        msg = str(exc)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            raise RuntimeError(
                "Gemini image API 配額或速率已用完，"
                "請到 https://ai.google.dev/gemini-api/docs/rate-limits 與 "
                "https://ai.dev/usage 檢查專案的計費與配額設定。"
            ) from exc
        raise

    images: List[bytes] = []
    limit = max(1, n)
    for img in _iter_images(resp):
        images.append(img)
        if len(images) >= limit:
            break

    if not images:
        raise RuntimeError("Gemini image API did not return any image data.")
    return images


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
    "openai": {
        "match": lambda m: True,
        "client_getter": get_openai_client,
        "payload_builder": build_openai_payload,
        "call_fn": call_openai,
    },
}


_IMAGE_BACKENDS: Dict[str, Dict[str, Any]] = {
    "stability": {
        "match": lambda m: m.lower().startswith("stability"),
        "client_getter": get_stability_client,
        "call_fn": call_stability_image,
    },
    "gemini": {
        "match": lambda m: m.lower().startswith("gemini") and "image" in m.lower(),
        "client_getter": get_gemini_client,
        "call_fn": call_gemini_image,
    },
    "openai": {
        "match": lambda m: True,
        "client_getter": get_openai_client,
        "call_fn": call_openai_image,
    },
}


def _resolve_backend(model_name: str) -> tuple[str, Dict[str, Any]]:
    for name, cfg in _BACKENDS.items():
        if cfg["match"](model_name):
            return name, cfg
    raise RuntimeError(f"No text backend matched {model_name}")


def _resolve_image_backend(model_name: str) -> tuple[str, Dict[str, Any]]:
    for name, cfg in _IMAGE_BACKENDS.items():
        if cfg["match"](model_name):
            return name, cfg
    raise RuntimeError(f"No image backend matched {model_name}")


def _invoke_once(
    model: str,
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    backend_name: str,
    backend_cfg: Dict[str, Any],
    timeout: int,
) -> str:
    client = backend_cfg["client_getter"]()
    payload = backend_cfg["payload_builder"](system_prompt, user_text, messages)
    return backend_cfg["call_fn"](client, model, payload, timeout)


# 統一 image 入口：回傳 List[bytes] -----------------------------------------

def generate_image(
    model: str,
    prompt: str,
    size: str = "1024x1024",
    n: int = 1,
    image_bytes: Optional[bytes] = None,
) -> List[bytes]:
    if not model:
        raise ValueError("Image model name must not be empty.")
    model_name = model.strip()
    backend_name, backend_cfg = _resolve_image_backend(model_name)
    client = backend_cfg["client_getter"]()
    if backend_name == "stability":
        return backend_cfg["call_fn"](
            client,
            model_name,
            prompt,
            size,
            n,
            init_image=image_bytes,
        )
    return backend_cfg["call_fn"](client, model_name, prompt, size, n)


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
                backend_name=backend_name,
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
