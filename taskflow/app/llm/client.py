"""app 侧装配层:shared 提供纯组件,这里用 taskflow 自己的 settings 拼出单例。

为什么保留这个文件、shared 里却没有单例?
- shared/llm/client.py 提供通用的 LLMClient / LLMResult / LLMToolCall;
- "用哪个 api_key、哪个模型"是 app 的配置,shared 绝不持有配置实例(见 shared/__init__.py),
  所以单例 llm_client 必须在 app 里用 taskflow 的 settings 构造;
- get_chat_model 同理:它返回的是"TaskFlow 偏好的 LangChain 模型"。

外部代码照旧 from app.llm.client import llm_client / get_chat_model / LLMResult,
不需要感知背后的拆分。
"""
from functools import lru_cache

from app.core.config import settings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from shared.llm.client import LLMClient, LLMResult, LLMToolCall

__all__ = [
    "LLMClient",
    "LLMResult",
    "LLMToolCall",
    "llm_client",
    "get_chat_model",
]


llm_client = LLMClient(config=settings)


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """创建并缓存 TaskFlow 使用的 LangChain ChatModel。

    为什么返回 BaseChatModel:
    Service 层依赖 LangChain 的模型抽象,而非强依赖具体厂商。

    为什么缓存:
    ChatModel 本身可复用,没必要每次请求都新建一个模型客户端对象。
    """
    return ChatOpenAI(
        # 模型名称,例如 deepseek-v4-pro
        model=settings.llm_model_id,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        # 普通助手场景先使用较低温度,后续可抽进配置。
        temperature=0.2,
        # 网络调用超时时间。
        timeout=60,
        # 遇到临时网络/API 错误最多自动重试 2 次。
        max_retries=2,
    )