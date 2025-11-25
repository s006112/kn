"""
模块流程：
环境变量加载 -> 客户端单例懒初始化 -> 文本规范化工具 -> 消息构建器
-> 后端调用（OpenAI / Perplexity） -> 路由选择 -> `call_llm` 入口

简单说明：大多数使用者调用 `call_llm(...)` 用于文本生成或 `generate_image(...)`
用于图像生成。内部私有 getters 管理客户端生命周期。
"""

import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import openai
from perplexity import Perplexity
from dotenv import load_dotenv

from utils_text_processing import _format_text, _normalize_output

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

# ============================================================================
# 全局客户端单例
# ============================================================================
# 这些在首次使用时懒初始化，以避免不必要的 API 连接
_OPENAI_CLIENT: Optional[openai.OpenAI] = None  # OpenAI 客户端单例
_PPLX_CLIENT: Optional[Any] = None  # Perplexity 客户端单例

# ============================================================================
# LLM 故障的自定义异常
# ============================================================================
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


# ============================================================================
# 客户端初始化函数（懒惰单例模式）
# ============================================================================
# 这些函数确保我们为每个后端提供商创建并重用单个客户端实例。
# 这减少了连接开销，并且是线程安全的。

def get_openai_client() -> openai.OpenAI:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        _OPENAI_CLIENT = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _OPENAI_CLIENT


def get_perplexity_client() -> Any:
    global _PPLX_CLIENT
    if _PPLX_CLIENT is None:
        if not PERPLEXITY_API_KEY:
            raise RuntimeError("PERPLEXITY_API_KEY environment variable is not set.")
        _PPLX_CLIENT = Perplexity(api_key=PERPLEXITY_API_KEY)
    return _PPLX_CLIENT


# ============================================================================
# 消息格式生成器
# ============================================================================
# 将输入参数转换为后端特定的有效负载格式。
# 不同的 LLM API 期望不同的消息结构。

def _build_response(
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    block_fn: Callable[[str, str], dict],
) -> List[Dict[str, Any]]:
    """
    通用消息构建器，支持自定义 block 格式。
    block_fn: (role, content) -> dict
    """
    if messages:
        payload = [
            block_fn(
                (m.get("role") or "user").strip() or "user",
                _format_text(m.get("content")),
            )
            for m in messages
        ]
        return payload or [block_fn("user", "")]
    payload: List[dict] = []
    if system_prompt:
        payload.append(block_fn("system", _format_text(system_prompt)))
    if user_text:
        payload.append(block_fn("user", _format_text(user_text)))
    return payload or [block_fn("user", "")]


def _build_openai_responses_payload(
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    为 OpenAI 的 Responses API 格式生成消息有效负载。
    """
    def block(role: str, value: str) -> Dict[str, Any]:
        return {"role": role, "content": [{"type": "input_text", "text": value}]}
    return _build_response(system_prompt, user_text, messages, block)


def _build_pplx_responses_payload(
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    为 Perplexity Chat Completions API 格式生成消息有效负载。
    """
    def block(role: str, value: str) -> Dict[str, str]:
        return {"role": role, "content": value}
    return _build_response(system_prompt, user_text, messages, block)


# ============================================================================
# 统一后端调用（去重后的单一路径）
# ============================================================================

def _invoke_backend_generic(
    backend_name: str,
    client: Any,
    model: str,
    payload: Any,
    timeout: int,
) -> str:
    """
    适配层：根据 backend_name 调用相应 SDK 方法并返回统一的字符串输出。
    """
    if backend_name == "openai":
        resp = client.responses.create(model=model, input=payload, timeout=timeout)
        out = getattr(resp, "output_text", None)
        if not isinstance(out, str):
            raise RuntimeError("Primary backend response did not contain text.")
        return _normalize_output(out)

    if backend_name == "perplexity":
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=payload,
                timeout=timeout,
            )
        except TypeError:
            # 某些 SDK 版本不支持 timeout 参数
            resp = client.chat.completions.create(
                model=model,
                messages=payload,
            )

        if not resp or not getattr(resp, "choices", None):
            raise RuntimeError("Perplexity returned no content.")

        content = getattr(resp.choices[0].message, "content", "")
        text = _normalize_output(content)
        if not text:
            raise RuntimeError("Perplexity response content was empty.")
        return text

    raise RuntimeError(f"Unsupported backend: {backend_name}")


# ============================================================================
# 后端注册表和路由（精简版）
# ============================================================================
# 定义所有可用的 LLM 后端以及如何将模型路由到它们。

_BACKENDS: Dict[str, Dict[str, Any]] = {
    "perplexity": {
        # 将以 "sonar" 开头的模型路由到 Perplexity（例如 "sonar-pro"、"sonar-reasoning"）
        "match": lambda name: name.lower().startswith("sonar"),
        "client_getter": get_perplexity_client,
        "payload_builder": _build_pplx_responses_payload,
    },
    "openai": {
        # OpenAI 是所有不匹配其他后端的模型的默认备用
        "match": lambda name: True,  # 始终匹配（默认）
        "client_getter": get_openai_client,
        "payload_builder": _build_openai_responses_payload,
    },
}


def _resolve_backend(model_name: str) -> tuple[str, Dict[str, Any]]:
    """
    返回 (backend_name, backend_cfg)。
    backend_cfg 即 _BACKENDS[backend_name] 的 dict。
    """
    for name, cfg in _BACKENDS.items():
        try:
            if cfg["match"](model_name):
                return name, cfg
        except Exception:
            continue
    return "openai", _BACKENDS["openai"]


def _invoke_backend(
    backend_name: str,
    backend_cfg: Dict[str, Any],
    model: str,
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    timeout: int,
) -> str:
    """
    高层入口：取 client -> 拼 payload -> 调 generic。
    """
    client_getter = backend_cfg["client_getter"]
    payload_builder = backend_cfg["payload_builder"]

    client = client_getter()
    payload = payload_builder(system_prompt, user_text, messages)

    return _invoke_backend_generic(
        backend_name=backend_name,
        client=client,
        model=model,
        payload=payload,
        timeout=timeout,
    )


# ============================================================================
# 主 LLM 入口点，具有重试逻辑
# ============================================================================

def generate_image(
    model: str,
    prompt: str,
    size: str = "1024x1024",
    n: int = 1,
) -> Any:
    """
    为 OpenAI 图像生成 API 的包装器。

    参数：
        model (str): 图像生成模型标识符（如 "dall-e-3"、"gpt-image-1"）
        prompt (str): 图像的文本描述
        size (str, 可选): 图像大小（如 "1024x1024"、"512x512"）
                          默认值: "1024x1024"
        n (int, 可选): 要生成的图像数量
                      默认值: 1

    返回值：
        OpenAI 图像生成 API 响应对象，包含 .data 属性（图像列表）

    抛出异常：
        RuntimeError: 如果 API 调用失败
        ValueError: 如果响应无效

    示例：
        >>> result = generate_image("dall-e-3", "A red car on a sunny day")
        >>> image_data = result.data[0]
        >>> url = image_data.url  # 或使用 image_data.b64_json
    """
    try:
        client = get_openai_client()
        return client.images.generate(
            model=model,
            prompt=prompt,
            size=size,
            n=n,
        )
    except Exception as exc:
        raise RuntimeError(f"Image generation request failed: {exc}") from exc


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
    所有 LLM 调用的统一入口点，具有自动后端路由和重试。

    输入优先级（只使用一个）：
    1. messages: 带有 role/content 的预构建消息列表
    2. 如果没有消息，使用 system_prompt + user_text

    重试行为：
    - 失败时等待 10 秒，然后重试
    - max_retries 表示“重试次数”，总尝试 = max_retries + 1
    - 所有尝试用尽后抛出 LLMPermanentFailure

    参数：
        model (str): 模型标识符（例如 "gpt-4"、"sonar-pro"、"gpt-3.5-turbo"）
                     不能为空
        system_prompt (str, 可选): 系统指令来指导模型行为
        user_text (str, 可选): 用户的查询/消息
        messages (Optional[Iterable[Dict]], 可选): 预构建消息列表
        file_path (str, 可选): 源文件路径用于日志记录/调试
        max_retries (int, 可选): 最大重试次数（默认 2）

    返回值：
        str: 模型的响应文本（清洁，删除了思考标签）

    抛出异常：
        ValueError: 如果模型名称为空
        LLMPermanentFailure: 在所有尝试后仍失败
    """
    if not model:
        raise ValueError("Model name must not be empty.")

    model_name = model.strip()

    backend_name, backend_cfg = _resolve_backend(model_name)

    timeout = 90  # seconds
    wait = 10     # seconds between retries

    total_attempts = max_retries + 1  # 修正：max_retries 为“重试次数”

    for attempt in range(total_attempts):
        try:
            return _invoke_backend(
                backend_name=backend_name,
                backend_cfg=backend_cfg,
                model=model_name,
                system_prompt=system_prompt,
                user_text=user_text,
                messages=messages,
                timeout=timeout,
            )
        except Exception as exc:
            if attempt == total_attempts - 1:
                raise LLMPermanentFailure(
                    f"Model API failed after {total_attempts} attempts for model "
                    f"{model_name} on file {file_path}: {exc}",
                    model=model_name,
                    backend=backend_name,
                    file_path=file_path,
                    reason=str(exc),
                ) from exc
            time.sleep(wait)
