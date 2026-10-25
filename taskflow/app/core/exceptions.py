"""TaskFlow 的业务异常。

跨项目通用的 AppException(错误响应基类)与 LLM 异常树已经搬到 shared.exceptions,
这里只留 TaskFlow 自己的业务异常。它们继承 shared.AppException,
所以 shared.exception_handlers 注册的 app_exception_handler 照样能捕获它们。
"""
from shared.exceptions import AppException


class TaskNotFoundError(AppException):
    def __init__(self):
        super().__init__(
            status_code=404,
            code="TASK_NOT_FOUND",
            message="Task not found",
        )


class TaskForbiddenError(AppException):
    def __init__(self):
        super().__init__(
            status_code=403,
            code="TASK_FORBIDDEN",
            message="You do not have permission to access this task",
        )


class UsernameAlreadyExistsError(AppException):
    def __init__(self):
        super().__init__(
            status_code=409,
            code="USERNAME_ALREADY_EXISTS",
            message="Username already exists",
        )


class InvalidCredentialsError(AppException):
    def __init__(self):
        super().__init__(
            status_code=401,
            code="INVALID_CREDENTIALS",
            message="Incorrect username or password",
            headers={
                "WWW-Authenticate": "Bearer"
            },
        )