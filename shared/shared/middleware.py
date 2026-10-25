"""请求日志中间件。

每个请求生成/透传 X-Request-ID，并记录 method/path/status/duration。
响应头同时返回 X-Request-ID 和 X-Process-Time-Ms，方便前端和日志关联。

try/except 包住 call_next，是为了同时覆盖成功和异常两条路径：
- 路由正常执行 → 记录 2xx/4xx/5xx 日志；
- 路由抛异常 → 记录 error 日志后重新 raise，交给全局异常处理器。
"""
import logging
import uuid
from time import perf_counter

from fastapi import Request

logger = logging.getLogger("app.request")


async def request_logging_middleware(request: Request, call_next):
    # 允许上游网关传入 request id；没有就生成一个短 id。
    request_id = (
        request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    )
    request.state.request_id = request_id

    start_time = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (perf_counter() - start_time) * 1000
        logger.error(
            "request method=%s path=%s status=500 "
            "duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            elapsed_ms,
            request_id,
        )
        raise

    elapsed_ms = (perf_counter() - start_time) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request method=%s path=%s status=%s "
        "duration_ms=%.2f request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request_id,
    )
    return response
