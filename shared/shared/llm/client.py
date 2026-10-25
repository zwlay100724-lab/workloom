"""OpenAI 兼容接口(DeepSeek)的通用封装。

职责:统一"超时 / 指数退避重试 / SDK 异常归一成 shared 的 LLM 异常"。
它不绑定任何业务,TaskFlow / DocMind 都用它。注意:
- config(api_key / 模型 / 重试参数)由调用方在构造时注入,本模块不 import settings;
- 模块级的 llm_client 单例住在各 app 自己的装配层(app/llm/client.py),
  这里不持有任何单例——因为单例需要 app 的 settings 才能建出来。
"""
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AsyncStream,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from shared.config import BaseConfig
from shared.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


@dataclass(slots=True)
class LLMToolCall:
    id: str
    name: str
    arguments: str


@dataclass(slots=True)
class LLMResult:
    content: str
    model: str

    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None

    tool_calls: list[LLMToolCall] = field(
        default_factory=list
    )


class LLMClient:
    """通用的 LLM 聊天客户端(重试 / 异常映射对 app 透明)。

    config 传入的是每个 app 自己的 Settings(BaseConfig 子类),
    这样同样的类可以被 TaskFlow 和 DocMind 各自实例化。
    """

    def __init__(self, *, config: BaseConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            # 整个请求(连接 + 读取)的最长等待时间,超时按临时故障重试。
            timeout=config.llm_timeout_seconds,
            # 关闭 SDK 自带的自动重试,统一交给 _request_with_retry 控制,
            # 避免两层重试叠加导致实际重试次数超出预期。
            max_retries=0,
        )
        self._model = config.llm_model_id

    @staticmethod
    def _build_error(exc: OpenAIError) -> LLMError:
        """把 OpenAI SDK 异常映射为 shared 自己的异常。

        注意 isinstance 的判定顺序:
        APITimeoutError 是 APIConnectionError 的子类,
        RateLimitError / InternalServerError / BadRequestError 都是 APIStatusError 的子类,
        所以必须把具体的类型放在通用的类型之前。
        """
        if isinstance(exc, APITimeoutError):
            return LLMTimeoutError(
                "LLM request timed out"
            )
        if isinstance(exc, RateLimitError):
            return LLMRateLimitError(
                "LLM rate limit exceeded"
            )
        if isinstance(exc, (APIConnectionError, InternalServerError)):
            return LLMServiceUnavailableError(
                "LLM service unavailable"
            )
        # 以下属于确定性错误(参数/鉴权/权限/资源不存在),重试没有意义。
        if isinstance(exc, AuthenticationError):
            return LLMError(
                "LLM API key is invalid or expired"
            )
        if isinstance(exc, (BadRequestError, PermissionDeniedError, NotFoundError)):
            return LLMError(
                f"LLM rejected the request: {exc}"
            )
        if isinstance(exc, APIStatusError):
            return LLMError(
                f"LLM API error (HTTP {exc.status_code})"
            )
        return LLMError(
            f"LLM API error: {exc}"
        )

    @staticmethod
    def _is_retryable(error: LLMError) -> bool:
        """超时 / 限流 / 网络 / 5xx 属于临时故障,值得重试。"""
        return isinstance(
            error,
            (LLMTimeoutError, LLMRateLimitError, LLMServiceUnavailableError),
        )

    async def _request_with_retry(
        self,
        **kwargs: Any,
    ) -> ChatCompletion | AsyncStream[ChatCompletionChunk]:
        """
        所有 LLM 请求(流式 / 非流式)统一从这里出去。
        负责:
        1. 调用模型
        2. 识别临时故障
        3. 指数退避重试
        4. 最终转换为 shared 自己的异常

        流式请求返回 AsyncStream,非流式返回 ChatCompletion。
        """
        max_retries = self._config.llm_max_retries
        base_delay = self._config.llm_retry_base_delay

        for attempt in range(max_retries + 1):

            try:
                # 真正调用外部 LLM 服务。
                return await self._client.chat.completions.create(
                    **kwargs
                )

            except OpenAIError as exc:
                error = self._build_error(exc)

                # 确定性错误(参数 / 鉴权 / 格式…)不重试,直接抛出。
                if not self._is_retryable(error):
                    raise error from exc

                # 已经用完所有重试机会。
                if attempt >= max_retries:
                    raise error from exc

                # 到这里说明:
                # 当前错误还允许进行下一次 retry。
                #
                # attempt = 0 → 0.5 秒
                # attempt = 1 → 1.0 秒
                # attempt = 2 → 2.0 秒
                delay = base_delay * (2 ** attempt)

                # async 代码绝对不要 time.sleep(),
                # 否则会阻塞事件循环。
                await asyncio.sleep(delay)

    async def stream_chat(
            self,
            *,
            messages: list[dict],
            temperature: float = 0.2,
            max_tokens: int = 1000,
            usage: dict[str, int | str] | None = None,
    ) -> AsyncIterator[str]:
        """流式对话。

        usage 不为 None 时,请求会带上 stream_options.include_usage,
        服务端在最后一个数据块返回整个请求的 token 用量,由这里写入 usage。

        异常策略:
        1. 建立连接阶段(create 本身)和普通请求一样,走 _request_with_retry,
           超时 / 网络 / 限流 / 5xx 会在这里退避重试;
        2. 已经开始逐块读取后,流中断(断连 / 超时 / 限流)无法安全重放——
           因为前面可能已经 yield 了部分内容,重放会造成内容重复,
           所以这里不重试,只把 SDK 异常映射为 shared 的异常后向上抛。
        """

        kwargs = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if usage is not None:
            kwargs["stream_options"] = {"include_usage": True}

        # 建立连接阶段:允许重试。
        stream = await self._request_with_retry(**kwargs)

        try:
            async for chunk in stream:
                content = (
                    chunk.choices[0].delta.content
                    if chunk.choices
                    else None
                )
                if content:
                    yield content
                # 最后一个块(choices 为空)才带 usage
                if usage is not None and chunk.usage is not None:
                    usage["input_tokens"] = chunk.usage.prompt_tokens
                    usage["output_tokens"] = chunk.usage.completion_tokens
                    usage["total_tokens"] = chunk.usage.total_tokens
                    usage["model"] = chunk.model

        except (APITimeoutError, TimeoutError) as exc:
            # 读取阶段超时,不重试(见 docstring 说明)。
            raise LLMTimeoutError(
                "LLM stream timed out"
            ) from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(
                "LLM rate limit exceeded during stream"
            ) from exc
        except (APIConnectionError, InternalServerError, ConnectionError) as exc:
            # 断连 / 5xx / 底层网络异常,读取阶段不重试。
            raise LLMServiceUnavailableError(
                "LLM connection lost during stream"
            ) from exc
        except OpenAIError as exc:
            # 其余 SDK 异常,统一转换为 shared 的异常,避免泄漏原始错误。
            raise LLMError(
                f"LLM stream failed: {exc}"
            ) from exc

    async def chat(
            self,
            messages: list[dict],
            *,
            temperature: float = 0.2,
            max_tokens: int = 1000,
            response_format: dict | None = None,
            tools: list[dict] | None = None,
            tool_choice: str | dict | None = None,
    ) -> LLMResult:
        """
        所有非流式 LLM 请求的统一入口。

        普通聊天、Structured Output、Tool Calling
        都复用这个方法,只是传入的可选参数不同。
        """
        # 1. 所有请求都一定需要的基础参数。
        kwargs = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # 2. Structured Output 才需要。
        # 普通聊天不要发送无意义参数。
        if response_format is not None:
            kwargs["response_format"] = response_format
        # 3. Tool Calling 才需要
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        # 4. 不直接调用 SDK。
        # 所有 Timeout / Retry / Backoff 统一走这一层。
        response = await self._request_with_retry(**kwargs)
        # 正常情况下 choices 一定非空;保险起见仍要防御,
        # 否则直接取 [0] 会抛 IndexError,错误信息不可读。
        if not response.choices:
            raise LLMError("LLM returned an empty response")

        message = response.choices[0].message
        # 5. 将 SDK 的 tool_calls 转换成
        # 自己定义的 LLMToolCall。
        tool_calls: list[LLMToolCall] = []
        if message.tool_calls:
            for tool_call in message.tool_calls:
                tool_calls.append(
                    LLMToolCall(
                        id=tool_call.id,
                        name=tool_call.function.name,
                        arguments=tool_call.function.arguments,
                    )
                )

        usage = response.usage
        # 6. 最终统一转换为 LLMResult。
        # Service 层以后不需要理解 SDK Response。
        return LLMResult(
            message.content or "",
            model=response.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            tool_calls=tool_calls,
        )