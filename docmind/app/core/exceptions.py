"""DocMind 的业务异常。

继承 shared.AppException，shared.exception_handlers 注册的
app_exception_handler 会自动把它们转成统一的错误响应。

归属不通过时统一返回 404（而不是 403）：
不向调用方暴露"这个资源存在，只是不属于你"。
"""
from shared.exceptions import AppException


class DocumentNotFoundError(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="DOCUMENT_NOT_FOUND",
            message="Document not found",
        )


class ConversationNotFoundError(AppException):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="CONVERSATION_NOT_FOUND",
            message="Conversation not found",
        )
