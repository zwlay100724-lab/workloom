"""跨项目通用的异常基座。

这里放的是"通用 HTTP 错误基类 + LLM 调用相关异常"——
它们描述"服务怎么报错",与具体业务无关,TaskFlow 和 DocMind 都用得上。

TaskFlow 的业务异常(TaskNotFoundError / UsernameAlreadyExistsError 等)
属于各自 app,仍留在 app/core/exceptions.py。
"""


class AppException(Exception):
    """统一错误响应基类:任何要返回给前端的业务错误都继承它。

    status_code / code 分别对应响应的 HTTP 状态与机器可读错误码。
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers

        super().__init__(message)


# ───────────────────────────────────────────────────────────────────
# LLM 异常树
# ───────────────────────────────────────────────────────────────────

class LLMStructuredOutputError(Exception):
    """LLM 返回的结构化数据校验失败(见 llm_service 的降级重试逻辑)。"""


class LLMError(Exception):
    """所有 LLM 相关异常的基础类型。"""


class LLMTimeoutError(LLMError):
    """调用模型超过最大等待时间。"""


class LLMRateLimitError(LLMError):
    """模型服务发生限流。"""


class LLMServiceUnavailableError(LLMError):
    """模型服务暂时不可用。"""


class LLMInvalidOutputError(LLMError):
    """LLM 返回的数据不符合系统要求。"""


class LLMToolExecutionError(LLMError):
    """Tool 在真实业务系统中执行失败。"""