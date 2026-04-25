import base64
from typing import Any, Dict, List, Optional

import openai
import requests
from google.genai import errors as genai_errors

from helper.helper_llm import (
    get_gemini_client,
    get_grok_client,
    get_openai_client,
    get_stability_client,
)


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


def _collect_image_inputs(
    init_image: Optional[bytes],
    init_images: Optional[List[bytes]] = None,
    max_items: Optional[int] = None,
) -> List[bytes]:
    image_inputs = [img for img in (init_images or []) if img]
    if not image_inputs and init_image is not None:
        image_inputs = [init_image]
    if max_items is not None:
        image_inputs = image_inputs[:max_items]
    return image_inputs


def _detect_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _get_grok_max_input_images(model: str) -> int:
    return 5


def _build_stability_payload(
    cfg: Dict[str, str],
    prompt: str,
    init_image: Optional[bytes],
    image_strength: Optional[float],
    model_key: str,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    variant = cfg["variant"]
    strength = 0.35 if image_strength is None else image_strength
    if variant == "generate":
        files: Dict[str, Any] = {
            "prompt": (None, prompt),
            "output_format": (None, "png"),
        }
        if init_image is not None:
            files.update(
                {
                    "mode": (None, "image-to-image"),
                    "image": ("init.png", init_image, "image/png"),
                    "strength": (None, str(strength)),
                }
            )
            print(
                "Stability image-to-image mode,"
                f" init_image size = {len(init_image)}, strength = {strength}",
            )
        else:
            files["mode"] = (None, "text-to-image")
            print("Stability text-to-image mode")
        return files, None

    if variant == "control":
        if init_image is None:
            raise RuntimeError(f"{model_key} requires an input image for control generation.")
        print(
            "Stability control mode,"
            f" model={model_key}, init_image size = {len(init_image)},"
            f" control_strength = {strength}",
        )
        return (
            {"image": ("init.png", init_image, "image/png")},
            {
                "prompt": prompt,
                "output_format": "png",
                "control_strength": str(strength),
            },
        )

    raise RuntimeError(f"Unsupported Stability variant '{variant}' for model {model_key}")


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


def call_grok_i2i(
    client: openai.OpenAI,
    model: str,
    prompt: str,
    size: str,
    n: int,
    init_image: bytes,
    init_images: Optional[List[bytes]] = None,
) -> List[bytes]:
    """Image-to-image for xAI, max-quality config enabled."""
    del size  # xAI uses aspect_ratio + resolution, not OpenAI-style size.

    api_key = getattr(client, "api_key", None)
    if not api_key:
        raise RuntimeError("Grok client is missing api_key.")

    def _extract_image_bytes(item: Any) -> Optional[bytes]:
        if item is None:
            return None
        if isinstance(item, (bytes, bytearray, memoryview)):
            return bytes(item)

        b64 = getattr(item, "b64_json", None)
        if b64 is None and isinstance(item, dict):
            b64 = item.get("b64_json")
        if b64:
            try:
                return base64.b64decode(b64)
            except Exception as exc:
                raise RuntimeError("Grok image payload b64_json is not valid base64.") from exc

        image_blob = getattr(item, "image", None)
        if image_blob is None and isinstance(item, dict):
            image_blob = item.get("image")
        if isinstance(image_blob, (bytes, bytearray, memoryview)):
            return bytes(image_blob)
        if isinstance(image_blob, str):
            try:
                return base64.b64decode(image_blob)
            except Exception:
                pass

        image_url = getattr(item, "url", None)
        if image_url is None and isinstance(item, dict):
            image_url = item.get("url")
        if image_url:
            r = requests.get(image_url, timeout=120)
            r.raise_for_status()
            return r.content
        return None

    def _to_xai_data_uri(img: bytes) -> str:
        mime = _detect_image_mime(img)
        if mime not in ("image/png", "image/jpeg"):
            try:
                from io import BytesIO
                from PIL import Image

                src = BytesIO(img)
                dst = BytesIO()
                Image.open(src).convert("RGBA").save(dst, format="PNG")
                img = dst.getvalue()
                mime = "image/png"
            except Exception as exc:
                raise RuntimeError(
                    "Grok image editing supports PNG/JPEG inputs. "
                    "Failed to normalize uploaded image to PNG."
                ) from exc

        return f"data:{mime};base64,{base64.b64encode(img).decode('utf-8')}"

    count = max(1, n)
    image_inputs = _collect_image_inputs(init_image, init_images)
    max_images = _get_grok_max_input_images(model)

    if not image_inputs:
        raise RuntimeError("Grok image editing requires an input image.")
    if len(image_inputs) > max_images:
        raise RuntimeError(
            f"Grok image editing supports up to {max_images} input images; "
            f"got {len(image_inputs)}."
        )

    image_data_uris = [_to_xai_data_uri(img) for img in image_inputs]
    primary_image_uri = image_data_uris[0]

    sdk_error: Optional[str] = None
    try:
        import xai_sdk  # type: ignore

        try:
            sdk_client = xai_sdk.Client(api_key=api_key)
        except TypeError:
            sdk_client = xai_sdk.Client()

        request_kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "model": model,
            "resolution": "2k",
            "image_format": "base64",
        }

        if len(image_data_uris) == 1:
            request_kwargs["image_url"] = primary_image_uri
        else:
            request_kwargs["image_urls"] = image_data_uris

        responses: List[Any] = []
        if count == 1:
            responses = [sdk_client.image.sample(**request_kwargs)]
        else:
            try:
                responses = list(sdk_client.image.sample_batch(**request_kwargs, n=count))
            except Exception:
                responses = [
                    sdk_client.image.sample(**request_kwargs)
                    for _ in range(count)
                ]

        sdk_images = [img for item in responses if (img := _extract_image_bytes(item))]
        if sdk_images:
            return sdk_images

        sdk_error = "xAI SDK returned no decodable image."
    except ImportError as exc:
        sdk_error = f"xAI SDK unavailable ({exc})."
    except Exception as exc:
        sdk_error = f"xAI SDK image.sample failed: {exc}"

    base_url = str(getattr(client, "base_url", "https://api.x.ai/v1")).rstrip("/")
    url = f"{base_url}/images/edits"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    body: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": count,
        "resolution": "2k",
        "response_format": "b64_json",
    }

    if len(image_data_uris) == 1:
        body["image"] = {
            "url": primary_image_uri,
            "type": "image_url",
        }
    else:
        body["images"] = [
            {
                "url": image_uri,
                "type": "image_url",
            }
            for image_uri in image_data_uris
        ]

    resp = requests.post(url, headers=headers, json=body, timeout=120)
    if resp.status_code != 200:
        detail = f" (sdk fallback note: {sdk_error})" if sdk_error else ""
        raise RuntimeError(
            f"Grok image edit error {resp.status_code}: {resp.text[:300]}{detail}"
        )

    payload = resp.json()
    data = payload.get("data") or []
    if not data:
        detail = f" (sdk fallback note: {sdk_error})" if sdk_error else ""
        raise RuntimeError(f"Grok image edit API returned no data.{detail}")

    images = [img for item in data if (img := _extract_image_bytes(item))]
    if not images:
        detail = f" (sdk fallback note: {sdk_error})" if sdk_error else ""
        raise RuntimeError(f"Grok image edit API returned no decodable image.{detail}")

    return images

def call_openai_i2i(
    client: openai.OpenAI,
    model: str,
    prompt: str,
    size: str,
    n: int,
    init_image: bytes,
    init_images: Optional[List[bytes]] = None,
) -> List[bytes]:
    """OpenAI image edit/reference generation. Transport adapter only."""
    api_key = getattr(client, "api_key", None)
    if not api_key:
        raise RuntimeError("OpenAI client is missing api_key.")

    model_name = model.strip()
    model_key = model_name.lower()
    image_inputs = _collect_image_inputs(init_image, init_images, max_items=16)

    if not image_inputs:
        raise RuntimeError("OpenAI image editing requires at least one input image.")

    if not prompt or not prompt.strip():
        raise RuntimeError("OpenAI image editing requires a non-empty prompt.")

    allowed_sizes = {"auto", "1024x1024", "1536x1024", "1024x1536"}
    openai_size = size if size in allowed_sizes else "auto"

    def to_openai_file(idx: int, img: bytes) -> tuple[str, bytes, str]:
        mime = _detect_image_mime(img)

        if mime == "image/png":
            return f"input_{idx}.png", img, mime
        if mime == "image/jpeg":
            return f"input_{idx}.jpg", img, mime
        if mime == "image/webp":
            return f"input_{idx}.webp", img, mime

        # OpenAI GPT image edit supports png/jpg/webp inputs.
        # Convert unsupported UI uploads such as bmp/tiff to png.
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(img)) as im:
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")

            out = BytesIO()
            im.save(out, format="PNG")
            png_bytes = out.getvalue()

        return f"input_{idx}.png", png_bytes, "image/png"

    image_files = [to_openai_file(idx, img) for idx, img in enumerate(image_inputs)]

    for filename, data, _mime in image_files:
        if len(data) > 50 * 1024 * 1024:
            raise RuntimeError(f"OpenAI image input exceeds 50MB: {filename}")

    files: List[tuple[str, Any]] = [
        ("model", (None, model_name)),
        ("prompt", (None, prompt.strip())),
        ("n", (None, str(max(1, min(n, 10))))),
        ("size", (None, openai_size)),
        ("quality", (None, "high")),
        ("background", (None, "opaque")),
        ("output_format", (None, "png")),
    ]

    # gpt-image-2 already processes image inputs at high fidelity.
    # Avoid passing optional fidelity knobs unless the model family clearly supports them.
    if model_key in {"gpt-image-1", "gpt-image-1.5", "chatgpt-image-latest"}:
        files.append(("input_fidelity", (None, "high")))

    for filename, data, mime in image_files:
        files.append(("image[]", (filename, data, mime)))

    resp = requests.post(
        "https://api.openai.com/v1/images/edits",
        headers={"Authorization": f"Bearer {api_key}"},
        files=files,
        timeout=240,
    )

    request_id = resp.headers.get("x-request-id", "")
    print(
        "DEBUG: OpenAI image edit | "
        f"request_id={request_id or '-'} | "
        f"model={model_name} | "
        f"inputs={len(image_files)} | "
        f"size={openai_size} | "
        f"quality=high | "
        f"prompt_chars={len(prompt.strip())}"
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenAI image edit error {resp.status_code}"
            f"{f' request_id={request_id}' if request_id else ''}: "
            f"{resp.text[:500]}"
        )

    payload = resp.json()
    data = payload.get("data") or []
    if not data:
        raise RuntimeError(
            f"OpenAI image edit API returned no data"
            f"{f' request_id={request_id}' if request_id else ''}."
        )

    images: List[bytes] = []
    for item in data:
        b64 = item.get("b64_json")
        if b64:
            images.append(base64.b64decode(b64))
            continue

        url = item.get("url")
        if url:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            images.append(r.content)

    if not images:
        raise RuntimeError(
            f"OpenAI image edit API did not return any decodable image"
            f"{f' request_id={request_id}' if request_id else ''}."
        )

    return images

def call_gemini_image(
    client: Any,
    model: str,
    prompt: str,
    size: str,
    n: int,
    init_image: Optional[bytes] = None,
    init_images: Optional[List[bytes]] = None,
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

    image_inputs = _collect_image_inputs(init_image, init_images)
    if image_inputs:
        parts: List[Dict[str, Any]] = [
            {
                "inline_data": {
                    "mime_type": "image/png",
                    "data": img,
                }
            }
            for img in image_inputs
        ]
        parts.append({"text": prompt})
        contents: Any = [{"role": "user", "parts": parts}]
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
    "grok-image": {
        "match": lambda m: m.lower().startswith("grok"),
        "client_getter": get_grok_client,
        "call_fn": call_grok_i2i,
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
    image_bytes_list: Optional[List[bytes]] = None,
) -> List[bytes]:
    """Unified entry point for all image backends."""
    if not model:
        raise ValueError("Image model name must not be empty.")
    model_name = model.strip()
    backend_name, backend_cfg = _resolve_image_backend(model_name)
    client = backend_cfg["client_getter"]()
    images = [img for img in (image_bytes_list or []) if img]
    primary_image = images[0] if images else image_bytes

    if backend_name == "stability":
        return backend_cfg["call_fn"](
            client,
            model_name,
            prompt,
            size,
            n,
            init_image=primary_image,
        )

    if backend_name == "grok-image":
        if primary_image is None:
            raise RuntimeError("Grok image editing requires an input image.")
        print(f"DEBUG: Grok image edit, model={model_name}, bytes={len(primary_image)}")
        return call_grok_i2i(
            client,
            model_name,
            prompt,
            size,
            n,
            init_image=primary_image,
            init_images=images or None,
        )

    # OpenAI gpt-image-1 在這裡自動切換為 images/edits
    if backend_name == "openai" and primary_image is not None:
        if model_name.lower().startswith("gpt"):
            print(
                f"DEBUG: OpenAI image edit, model={model_name}, bytes={len(primary_image)}"
            )
            return call_openai_i2i(
                client,
                model_name,
                prompt,
                size,
                n,
                init_image=primary_image,
                init_images=images or None,
            )

    if backend_name == "gemini":
        return backend_cfg["call_fn"](
            client,
            model_name,
            prompt,
            size,
            n,
            init_image=primary_image,
            init_images=images or None,
        )

    return backend_cfg["call_fn"](client, model_name, prompt, size, n)


__all__ = ["generate_image"]
