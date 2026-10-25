"""FastAPI 全局异常处理器的统一注册入口。

TaskFlow 和 DocMind 都会用同一套"错误 → {success:false, error:{...}}"的
响应结构,所以整个文件原样提到 shared。它只依赖 shared.exceptions,
业务异常(继承 AppException 的那些)由各 app 自己定义、同样会被这里的
app_exception_handler 捕获。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from shared.exceptions import (
    AppException,
    LLMInvalidOutputError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMStructuredOutputError,
    LLMTimeoutError,
)

logger = logging.getLogger("app.error")

HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
}


def register_exception_handlers(app: FastAPI) -> None:
    """把全套异常处理器注册到 app 上(必须在 include_router 之前/后均可)。"""

    @app.exception_handler(AppException)
    async def app_exception_handler(
        request: Request,
        exc: AppException,
    ):
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "success": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            },
        )

    @app.exception_handler(LLMStructuredOutputError)
    async def llm_structured_output_error_handler(
        request: Request,
        exc: LLMStructuredOutputError,
    ):
        logger.warning(
            "LLM structured output validation failed: %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=502,
            content={
                "success": False,
                "error": {
                    "code": "LLM_STRUCTURED_OUTPUT_ERROR",
                    "message": (
                        str(exc)
                        or "LLM returned invalid structured data"
                    ),
                },
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ):
        code = HTTP_ERROR_CODES.get(
            exc.status_code,
            f"HTTP_{exc.status_code}",
        )
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "success": False,
                "error": {
                    "code": code,
                    "message": str(exc.detail),
                },
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ):
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": jsonable_encoder(exc.errors()),
                },
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_exception_handler(
        request: Request,
        exc: Exception,
    ):
        logger.exception(
            "Unhandled exception: %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "Internal server error",
                },
            },
        )

    @app.exception_handler(LLMTimeoutError)
    async def llm_timeout_handler(
        request: Request,
        exc: LLMTimeoutError,
    ):
        # 对客户端隐藏底层 SDK 细节,只返回业务语义。
        logger.warning(
            "LLM timeout: %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=504,
            content={
                "success": False,
                "error": {
                    "code": "LLM_TIMEOUT",
                    "message": "AI service timed out",
                },
            },
        )

    @app.exception_handler(LLMRateLimitError)
    async def llm_rate_limit_handler(
        request: Request,
        exc: LLMRateLimitError,
    ):
        logger.warning(
            "LLM rate limit: %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": {
                    "code": "LLM_RATE_LIMIT",
                    "message": "AI service rate limit exceeded",
                },
            },
        )

    @app.exception_handler(LLMServiceUnavailableError)
    async def llm_unavailable_handler(
        request: Request,
        exc: LLMServiceUnavailableError,
    ):
        logger.warning(
            "LLM service unavailable: %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": {
                    "code": "LLM_UNAVAILABLE",
                    "message": "AI service is temporarily unavailable",
                },
            },
        )

    @app.exception_handler(LLMInvalidOutputError)
    async def llm_invalid_output_handler(
        request: Request,
        exc: LLMInvalidOutputError,
    ):
        logger.warning(
            "LLM invalid output: %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=502,
            content={
                "success": False,
                "error": {
                    "code": "LLM_INVALID_OUTPUT",
                    "message": "AI service returned invalid data",
                },
            },
        )
