import os
import re
import time
import inspect
from typing import Any, Callable, Dict, Iterable, List, Optional
import openai
from perplexity import Perplexity
from dotenv import load_dotenv

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
    """
    在所有重试尝试都用尽后，当 LLM 后端失败时抛出。
    
    此异常在以下情况下抛出：
    - 所有 max_retries 次尝试都已用尽
    - 底层 API 或网络错误在重试过程中仍然存在
    
    属性：
        message (str): 人类可读的错误描述
        model (str): 失败的模型名称（例如 "gpt-4"、"sonar-2"）
        backend (str): 失败的后端提供商（例如 "openai"、"perplexity"）
        file_path (str): LLM 调用的源文件路径（可选）
        reason (str): 用于调试的原始异常消息/堆栈跟踪
    """

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

def _get_openai_client() -> openai.OpenAI:
    """
    获取或创建 OpenAI 客户端单例。
    
    首次调用时，使用环境变量中的 API 密钥初始化 OpenAI 客户端。
    后续调用返回缓存的实例。
    
    返回值：
        openai.OpenAI: 配置完毕、准备好进行 API 调用的 OpenAI 客户端
        
    抛出异常：
        RuntimeError: 如果未设置 OPENAI_API_KEY 环境变量
    """
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        _OPENAI_CLIENT = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _OPENAI_CLIENT


def _get_pplx_client() -> Any:
    """
    获取或创建 Perplexity 客户端单例。
    
    首次调用时，使用环境变量中的 API 密钥初始化 Perplexity SDK 客户端。
    后续调用返回缓存的实例。
    
    返回值：
        Any: 配置完毕、准备好进行 API 调用的 Perplexity 客户端
        
    抛出异常：
        RuntimeError: 如果未安装 Perplexity SDK 或 API 密钥缺失
    """
    global _PPLX_CLIENT
    if _PPLX_CLIENT is None:
        if not PERPLEXITY_API_KEY:
            raise RuntimeError("PERPLEXITY_API_KEY environment variable is not set.")
        _PPLX_CLIENT = Perplexity(api_key=PERPLEXITY_API_KEY)
    return _PPLX_CLIENT



# ============================================================================
# 文本处理工具
# ============================================================================
# 用于规范化和清理 LLM 输入和输出的辅助函数

def _format_text(v: Any) -> str:
    """
    将任何值转换为规范化的文本字符串。
    
    处理多种输入类型：
    - None → 空字符串
    - str → 去除前后空白符
    - 其他 → 转换为字符串，然后去除前后空白符
    
    参数：
        v (Any): 要转换的输入值
        
    返回值：
        str: 规范化的文本（始终小写，准备好进行处理）
    """
    if v is None:
        return ""
    return v.strip() if isinstance(v, str) else str(v).strip()


# ============================================================================
# HTML <think> 标签处理
# ============================================================================
# 用于从模型输出中移除 OpenAI 的推理标签。
# 某些模型（如 o1-preview）输出 <think>...</think> 标签，显示内部推理过程，
# 这些应该对用户隐藏。

# 正则表达式模式用于匹配开闭 <think> 标签（不区分大小写）
# 处理 <think>、< think>、<THINK>、</think> 等变体
_THINK_TAG = re.compile(r"<\s*(/?)\s*think\b[^>]*>", re.IGNORECASE)


def _strip_think(text: str) -> str:
    """
    从文本中移除 <think>...</think> 标签及其内容。
    
    正确处理：
    - 嵌套的 think 标签（跟踪深度）
    - 孤立的关闭标签（保留它们）
    - 格式错误的标签
    
    算法：
    1. 在解析标签时跟踪嵌套深度
    2. 只在不在 think 块内时输出文本
    3. 当进入 think 块（深度 > 0）时，跳过内容
    4. 当退出 think 块（深度回到 0）时，恢复输出
    
    参数：
        text (str): 可能包含 <think> 标签的输入文本
        
    返回值：
        str: 移除所有 <think> 标签和内容的文本
    """
    if not text or "<" not in text:
        return text
    
    depth, last, out = 0, 0, []
    
    # 遍历文本中找到的所有 <think> 和 </think> 标签
    for m in _THINK_TAG.finditer(text):
        s, e = m.span()  # 标签的开始和结束位置
        closing = bool(m.group(1))  # 如果这是关闭标签 (</think>)，则为真
        
        # 如果不在 think 块内，添加此标签之前的文本
        if depth == 0:
            out.append(text[last:s])
        
        # 处理开闭标签
        if not closing:
            if depth == 0:
                last = e  # 标记跳过内容的开始
            depth += 1
        # 处理关闭标签
        else:
            if depth > 0:
                depth -= 1
                if depth == 0:
                    last = e  # 标记跳过内容的结束
            else:
                # 孤立的关闭标签（没有匹配的开启） - 保留它
                out.append(m.group(0))
                last = e
    
    # 添加最后一个标签之后的任何剩余文本（当深度为 0 时）
    if depth == 0:
        out.append(text[last:])
    
    return "".join(out)


def _normalize_output(content: Any) -> str:
    """
    将 LLM 响应内容转换为干净的字符串。
    
    处理多种响应格式：
    - str: 直接字符串输出（最常见）
    - List/Iterable: 多个内容块（用换行符连接）
    - Dict: 提取 'text' 字段
    - Object: 提取 'text' 属性
    
    还从最终输出中移除 <think> 标签。
    
    参数：
        content (Any): 原始 LLM 响应内容（格式因提供商而异）
        
    返回值：
        str: 清洁、规范化的文本，准备好使用
    """
    if isinstance(content, str):
        text = content.strip()
    elif not content:
        # 处理空/无内容
        text = ""
    else:
        # 处理内容块列表/可迭代对象（例如来自 API 响应）
        parts: List[str] = []
        for c in content:
            # 首先尝试从对象属性提取文本，然后尝试字典键
            t = getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else None)
            if t:
                parts.append(str(t))
        text = "\n".join(parts).strip()
    
    # 移除思考标签并返回清洁文本
    return _strip_think(text)


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
    """
    调用 OpenAI 的 Responses API 并提取响应。
    
    这使用 OpenAI 更新的 Responses API，其格式不同于
    标准 Chat Completions API。
    
    参数：
        model (str): 模型名称（例如 "gpt-4"、"gpt-3.5-turbo"）
        system_prompt (str): 系统指令
        user_text (str): 用户消息
        messages (Optional): 预构建消息列表
        timeout (int): API 调用超时（秒）
        
    返回值：
        str: 规范化的模型响应
        
    抛出异常：
        RuntimeError: 如果响应不包含 text 字段
    """
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
    """
    调用 Perplexity 的 Chat Completions API 并提取响应。
    
    Perplexity.AI 提供启用网络搜索的 LLM 模型（Sonar 系列）。
    使用标准 Chat Completions 格式。
    
    特殊处理：
    - Perplexity SDK 可能不支持超时参数
    - 如果发生 TypeError，则回退到无超时的重试
    
    参数：
        model (str): 模型名称（例如 "sonar-pro"、"sonar-reasoning"）
        system_prompt (str): 系统指令
        user_text (str): 用户消息
        messages (Optional): 预构建消息列表
        timeout (int): API 调用超时（秒）（可能不支持）
        
    返回值：
        str: 规范化的模型响应
        
    抛出异常：
        RuntimeError: 如果响应为空或格式错误
    """
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
# Main LLM Entry Point with Retry Logic
# ============================================================================

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
# 公共 API：向后兼容导出
# ============================================================================
# 这些函数为需要直接访问的代码公开后端客户端。

def get_openai_client() -> openai.OpenAI:
    """
    获取 OpenAI 客户端单例用于直接 API 访问。
    
    仅在需要 call_llm 提供的超出直接 OpenAI API 访问时使用。
    大多数代码应该改用 call_llm()。
    
    返回值：
        openai.OpenAI: 配置完毕、准备好进行 API 调用的 OpenAI 客户端
        
    抛出异常：
        RuntimeError: 如果未设置 OPENAI_API_KEY
    """
    return _get_openai_client()


def get_perplexity_client() -> Any:
    """
    获取 Perplexity SDK 客户端单例用于直接 API 访问。
    
    仅在需要 call_llm 提供的超出直接 Perplexity API 访问时使用。
    大多数代码应该改用 call_llm()。
    
    返回值：
        Any: 配置完毕、准备好进行 API 调用的 Perplexity 客户端
        
    抛出异常：
        RuntimeError: 如果未安装 Perplexity SDK 或未设置 API 密钥
    """
    return _get_pplx_client()


# ============================================================================
# 模块导出
# ============================================================================
# 导入此模块的调用者可用的公共 API

__all__ = ["LLMPermanentFailure", "call_llm", "get_openai_client", "get_perplexity_client"]
