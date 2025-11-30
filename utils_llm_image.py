import base64
from typing import Any, Dict, List, Optional

import openai
import requests
from google.genai import errors as genai_errors

from utils_llm import get_gemini_client, get_openai_client, get_stability_client


_STABILITY_BASE_URL = "https://api.stability.ai/v2beta/stable-image"
_STABILITY_MODEL_CONFIG: Dict[str, Dict[str, str]] = {
    "stability-ultra": {
        "variant": "generate",
        "url": f"{_STABILITY_BASE_URL}/generate/ultra",
    },
    "stability-core": {
        "variant": "generate",
        "url": f"{_STABILITY_BASE_URL}/generate/core",
    },
    "stability-sd3": {
        "variant": "generate",
        "url": f"{_STABILITY_BASE_URL}/generate/sd3",
    },
    "stability-sketch": {
        "variant": "control",
        "url": f"{_STABILITY_BASE_URL}/control/sketch",
    },
    "stability-structure": {
        "variant": "control",
        "url": f"{_STABILITY_BASE_URL}/control/structure",
    },
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _build_stability_payload(
    cfg: Dict[str, str],
    prompt: str,
    init_image: Optional[bytes],
    image_strength: Optional[float],
    model_key: str,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    variant = cfg["variant"]
    if variant == "generate":
        return _build_generate_payload(prompt, init_image, image_strength)
    if variant == "control":
        return _build_control_payload(prompt, init_image, image_strength, model_key)
    raise RuntimeError(f"Unsupported Stability variant '{variant}' for model {model_key}")


def _build_generate_payload(
    prompt: str,
    init_image: Optional[bytes],
    image_strength: Optional[float],
) -> tuple[Dict[str, Any], None]:
    files: Dict[str, Any] = {
        "prompt": (None, prompt),
        "output_format": (None, "png"),
    }
    if init_image is not None:
        strength = _clamp01(0.35 if image_strength is None else image_strength)
        files["mode"] = (None, "image-to-image")
        files["image"] = ("init.png", init_image, "image/png")
        files["strength"] = (None, str(strength))
        print(
            "Stability image-to-image mode,"
            f" init_image size = {len(init_image)}, strength = {strength}",
        )
    else:
        files["mode"] = (None, "text-to-image")
        print("Stability text-to-image mode")
    return files, None


def _build_control_payload(
    prompt: str,
    init_image: Optional[bytes],
    image_strength: Optional[float],
    model_key: str,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    if init_image is None:
        raise RuntimeError(f"{model_key} requires an input image for control generation.")
    files: Dict[str, Any] = {
        "image": ("init.png", init_image, "image/png"),
    }
    data: Dict[str, Any] = {
        "prompt": prompt,
        "output_format": "png",
    }
    if image_strength is not None:
        data["control_strength"] = str(_clamp01(image_strength))
        print(
            "Stability control mode,"
            f" model={model_key}, init_image size = {len(init_image)},"
            f" control_strength = {data['control_strength']}",
        )
    else:
        print(
            "Stability control mode,"
            f" model={model_key}, init_image size = {len(init_image)},"
            " control_strength = default",
        )
    return files, data


# 圖像 backend 呼叫：統一回傳 List[bytes] -----------------------------------


def call_stability_image(
    api_key: str,
    model: str,
    prompt: str,
    size: str,
    n: int,
    init_image: Optional[bytes] = None,
    image_strength: Optional[float] = None,
) -> List[bytes]:
    """Invoke Stability.ai Stable Image v2beta for all supported variants."""
    del size  # Unused but kept for API compatibility.
    model_lower = model.lower().strip()
    cfg = _STABILITY_MODEL_CONFIG.get(model_lower)
    if not cfg:
        raise RuntimeError(f"Unknown Stability model alias: {model}")

    files, data = _build_stability_payload(cfg, prompt, init_image, image_strength, model_lower)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "image/*",
    }

    images: List[bytes] = []
    count = max(1, n)

    for _ in range(count):
        resp = requests.post(
            cfg["url"],
            headers=headers,
            files=files,
            data=data,
            timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Stability image API error {resp.status_code}: {resp.text[:200]}"
            )
        images.append(resp.content)

    return images


def call_openai_t2i(
    client: openai.OpenAI,
    model: str,
    prompt: str,
    size: str,
    n: int,
) -> List[bytes]:
    """Use OpenAI images.generate with graceful decoding of returned data."""
    resp = client.images.generate(model=model, prompt=prompt, size=size, n=n)

    data = getattr(resp, "data", None) or []
    if not data:
        raise RuntimeError("OpenAI image API returned no data.")

    def _extract_bytes(item: Any) -> Optional[bytes]:
        b64 = getattr(item, "b64_json", None)
        if b64:
            try:
                return base64.b64decode(b64)
            except Exception as exc:  # pragma: no cover - defensive
                raise RuntimeError("OpenAI image payload is not valid base64.") from exc

        url = getattr(item, "url", None)
        if not url:
            return None
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        return response.content

    images = [img for item in data if (img := _extract_bytes(item))]
    if not images:
        raise RuntimeError("OpenAI image API did not return any decodable image.")
    return images


def call_openai_i2i(
    client: openai.OpenAI,
    model: str,
    prompt: str,
    size: str,
    n: int,
    init_image: bytes,
) -> List[bytes]:
    """Call OpenAI /v1/images/edits for image-to-image editing."""
    api_key = getattr(client, "api_key", None)
    if not api_key:
        raise RuntimeError("OpenAI client is missing api_key.")

    url = "https://api.openai.com/v1/images/edits"

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    files = {
        "image": ("init.png", init_image, "image/png"),
        "model": (None, model),
        "prompt": (None, prompt),
        "n": (None, str(max(1, n))),
        "size": (None, size),
    }

    resp = requests.post(url, headers=headers, files=files, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenAI image edit error {resp.status_code}: {resp.text[:200]}"
        )

    payload = resp.json()
    data = payload.get("data") or []
    if not data:
        raise RuntimeError("OpenAI image edit API returned no data.")

    images: List[bytes] = []
    for item in data:
        b64 = item.get("b64_json")
        if b64:
            try:
                images.append(base64.b64decode(b64))
                continue
            except Exception:  # pragma: no cover - fallback to URL fetch
                pass
        url2 = item.get("url")
        if url2:
            r2 = requests.get(url2, timeout=120)
            r2.raise_for_status()
            images.append(r2.content)

    if not images:
        raise RuntimeError("OpenAI image edit API did not return any decodable image.")
    return images


def call_gemini_image(
    client: Any,
    model: str,
    prompt: str,
    size: str,
    n: int,
    init_image: Optional[bytes] = None,
) -> List[bytes]:
    """Invoke Gemini image-capable models and collect inline or remote bytes."""

    def _ensure_bytes(data: Any) -> bytes:
        if isinstance(data, (bytes, bytearray, memoryview)):
            return bytes(data)
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
        resp = requests.get(uri, timeout=120)
        resp.raise_for_status()
        return resp.content

    def _parts_to_images(parts: Optional[List[Any]]):
        for part in parts or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline else None
            if data:
                yield _ensure_bytes(data)
                continue
            file_data = getattr(part, "file_data", None)
            uri = getattr(file_data, "file_uri", None) if file_data else None
            if uri:
                yield _download(uri)

    def _iter_images(response: Any):
        for item in getattr(response, "generated_images", None) or []:
            image = getattr(item, "image", None)
            data = getattr(image, "image_bytes", None) if image else None
            if data:
                yield _ensure_bytes(data)
        for cand in getattr(response, "candidates", None) or []:
            content = getattr(cand, "content", None)
            if content:
                yield from _parts_to_images(getattr(content, "parts", None))

    if init_image is not None:
        contents: Any = [
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": init_image,
                        }
                    },
                    {"text": prompt},
                ],
            }
        ]
    else:
        contents = prompt

    try:
        resp = client.models.generate_content(model=model, contents=contents)
    except genai_errors.ClientError as exc:  # pragma: no cover - passthrough
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
        "call_fn": call_openai_t2i,
    },
}


def _resolve_image_backend(model_name: str) -> tuple[str, Dict[str, Any]]:
    for name, cfg in _IMAGE_BACKENDS.items():
        if cfg["match"](model_name):
            return name, cfg
    raise RuntimeError(f"No image backend matched {model_name}")


# 統一 image 入口 -------------------------------------------------------------

def generate_image(
    model: str,
    prompt: str,
    size: str = "1024x1024",
    n: int = 1,
    image_bytes: Optional[bytes] = None,
) -> List[bytes]:
    """Unified entry point for all image backends."""
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

    # OpenAI gpt-image-1 在這裡自動切換為 images/edits
    if backend_name == "openai" and image_bytes is not None:
        if model_name.lower().startswith("gpt-image-1"):
            print(
                f"DEBUG: OpenAI image edit, model={model_name}, bytes={len(image_bytes)}"
            )
            return call_openai_i2i(
                client,
                model_name,
                prompt,
                size,
                n,
                init_image=image_bytes,
            )

    if backend_name == "gemini":
        return backend_cfg["call_fn"](client, model_name, prompt, size, n, init_image=image_bytes)

    return backend_cfg["call_fn"](client, model_name, prompt, size, n)


__all__ = ["generate_image"]
