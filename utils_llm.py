import os
import time
import inspect
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

# 内部别名用于向后兼容
_get_openai_client = get_openai_client
_get_pplx_client = get_perplexity_client


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
) -> list:
    """
    通用消息构建器，支持自定义 block 格式。
    block_fn: (role, content) -> dict
    """
    if messages:
        built = [
            block_fn((m.get("role") or "user").strip() or "user", _format_text(m.get("content")))
            for m in messages
        ]
        return built or [block_fn("user", "")]
    built = []
    if system_prompt:
        built.append(block_fn("system", _format_text(system_prompt)))
    if user_text:
        built.append(block_fn("user", _format_text(user_text)))
    return built or [block_fn("user", "")]

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
) -> List[Dict[str, str]]:
    """
    为 Perplexity Chat Completions API 格式生成消息有效负载。
    """
    def block(role: str, value: str) -> Dict[str, str]:
        return {"role": role, "content": value}
    return _build_response(system_prompt, user_text, messages, block)


# ============================================================================
# 后端特定的调用函数
# ============================================================================
# 每个后端提供商都有自己的 API 格式和错误处理要求。

def _invoke_openai(
    model: str,
    system_prompt: str,
    user_text: str,
    messages: Optional[Iterable[Dict[str, Any]]],
    timeout: int,
) -> str:
    client = _get_openai_client()
    payload = _build_openai_responses_payload(system_prompt, user_text, messages)
    
    # 调用 OpenAI Responses API
    resp = client.responses.create(model=model, input=payload, timeout=timeout)
    
    # 从响应对象提取文本
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
    msg_payload = _build_pplx_responses_payload(system_prompt, user_text, messages)

    # 首先尝试带超时；某些 SDK 版本不支持它
    try:
        resp = client.chat.completions.create(model=model, messages=msg_payload, timeout=timeout)
    except TypeError:
        # 回退：无超时参数重试
        resp = client.chat.completions.create(model=model, messages=msg_payload)

    # 验证响应结构
    if not resp or not getattr(resp, "choices", None):
        raise RuntimeError("Perplexity returned no content.")
    
    # 从第一个选项的消息提取文本
    content = getattr(resp.choices[0].message, "content", "")
    text = _normalize_output(content)
    
    if not text:
        raise RuntimeError("Perplexity response content was empty.")
    
    return text



# ============================================================================
# 后端注册表和路由
# ============================================================================
# 定义所有可用的 LLM 后端以及如何将模型路由到它们。

# 后端注册表：只在此处添加新后端。
# 每个后端配置包含：
#   - match: 函数，用于确定模型名称是否属于此后端
#   - invoke: 函数，用于调用后端的 API
_BACKENDS: Dict[str, Dict[str, Any]] = {
    "perplexity": {
        # 将以 "sonar" 开头的模型路由到 Perplexity（例如 "sonar-pro"、"sonar-reasoning"）
        "match": lambda name: name.lower().startswith("sonar"),
        "invoke": _invoke_pplx,
    },
    "openai": {
        # OpenAI 是所有不匹配其他后端的模型的默认备用
        "match": lambda name: True,  # 始终匹配（默认）
        "invoke": _invoke_openai,
    },
}


def _resolve_backend(model_name: str) -> str:
    """
    确定为给定模型使用哪个后端提供商。
    
    检查每个后端的匹配函数，直到一个匹配。
    如果没有其他后端匹配，则回退到 "openai"。
    
    算法：
    1. 按定义顺序遍历已注册的后端
    2. 对于每个后端，使用模型名称调用其匹配函数
    3. 返回第一个匹配的后端（返回真值）
    4. 捕获异常以处理可能失败的匹配函数
    5. 如果没有后端匹配，返回 "openai"（默认）
    
    参数：
        model_name (str): 模型标识符（例如 "gpt-4"、"sonar-pro"）
        
    返回值：
        str: 后端名称（"openai"、"perplexity" 等）
    """
    for name, cfg in _BACKENDS.items():
        try:
            if cfg["match"](model_name):
                return name
        except Exception:
            # 如果匹配函数抛出错误，跳过此后端并尝试下一个
            continue
    return "openai"  # 默认备用


# ============================================================================
# 主 LLM 入口点，具有重试逻辑
# ============================================================================

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
    
    这是大多数代码应该调用的主函数。它：
    1. 将请求路由到适当的后端（OpenAI、Perplexity 等）
    2. 根据后端要求格式化输入
    3. 通过自动重试处理 API 调用失败
    4. 规范化和清理响应
    5. 在 max_retries 次尝试后抛出 LLMPermanentFailure
    
    输入优先级（只使用一个）：
    1. messages: 带有 role/content 的预构建消息列表
    2. 如果没有消息，使用 system_prompt + user_text
    
    重试行为：
    - 在 API 失败时，等待 10 秒，然后重试
    - 重试最多 max_retries 次
    - 所有重试用尽后，抛出 LLMPermanentFailure
    
    参数：
        model (str): 模型标识符（例如 "gpt-4"、"sonar-pro"、"gpt-3.5-turbo"）
                     不能为空
        system_prompt (str, 可选): 系统指令来指导模型行为
                                  默认值: ""
        user_text (str, 可选): 用户的查询/消息
                               默认值: ""
        messages (Optional[Iterable[Dict]], 可选): 预构建消息列表
                                                   每个字典必须有 'role' 和 'content'
                                                   默认值: None（使用 system_prompt + user_text）
        file_path (str, 可选): 源文件路径用于日志记录/调试
                               在错误消息中用于追踪调用来源
                               默认值: None
        max_retries (int, 可选): 最大尝试次数
                                 默认值: 2（意味着最多 2 次重试 = 3 次总尝试）
        
    返回值：
        str: 模型的响应文本（清洁，删除了思考标签）
        
    抛出异常：
        ValueError: 如果模型名称为空
        LLMPermanentFailure: 在 max_retries 次尝试后，如果所有都失败
                            包含：model、backend、file_path、reason 用于调试
        
    示例：
        >>> # 简单查询
        >>> response = call_llm("gpt-4", user_text="What is 2+2?")
        >>> 
        >>> # 带系统提示
        >>> response = call_llm(
        ...     "gpt-4",
        ...     system_prompt="You are a helpful math tutor.",
        ...     user_text="Explain calculus basics"
        ... )
        >>> 
        >>> # 带预构建消息（更多控制）
        >>> messages = [
        ...     {"role": "system", "content": "You are helpful."},
        ...     {"role": "user", "content": "Hello!"},
        ...     {"role": "assistant", "content": "Hi there!"},
        ...     {"role": "user", "content": "How are you?"}
        ... ]
        >>> response = call_llm("gpt-4", messages=messages)
        >>> 
        >>> # 带重试和文件跟踪
        >>> response = call_llm(
        ...     "sonar-pro",
        ...     user_text="Search for latest AI news",
        ...     file_path="news_scraper.py",
        ...     max_retries=3
        ... )
    """
    # 验证输入
    if not model:
        raise ValueError("Model name must not be empty.")
    
    model_name = model.strip()

    # 确定为此模型使用哪个后端
    invoke = _BACKENDS[_resolve_backend(model_name)]["invoke"]
    
    # 所有后端调用的 API 超时（以秒为单位）
    timeout = 90

    # 重试之间的固定退避时间（以秒为单位）
    wait = 10

    # 尝试 LLM 调用，带有重试
    for i in range(max_retries):
        try:
            # 调用适当的后端并在成功时返回
            return invoke(model_name, system_prompt, user_text, messages, timeout)
        except Exception as exc:
            # 检查这是否是最后一次重试尝试
            if i == max_retries - 1:
                # 所有重试用尽 - 抛出永久失败，并提供调试信息
                raise LLMPermanentFailure(
                    f"Model API failed after {max_retries} attempts for model {model_name} on file {file_path}: {exc}",
                    model=model_name,
                    backend=_resolve_backend(model_name),
                    file_path=file_path,
                    reason=str(exc),
                )
            # 不是最后一次尝试 - 重试前等待
            time.sleep(wait)


# ============================================================================
# 模块导出
# ============================================================================
# 导入此模块的调用者可用的公共 API

__all__ = ["LLMPermanentFailure", "call_llm", "get_openai_client", "get_perplexity_client"]
