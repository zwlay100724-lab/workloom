"""LLM 用量日志回调。

TaskFlow 有完整的 usage 落库链路；DocMind 目前没有 usage 表，
所以这里只做结构化日志：
- 每次 LLM 调用记录 model / input_tokens / output_tokens / latency；
- 一轮 RAG 结束后由 RAGService 汇总 calls/tokens，并按可选单价估算成本。
"""
import logging
from decimal import Decimal
from time import perf_counter
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult as LangChainResult

from app.core.config import settings

logger = logging.getLogger("docmind.llm")


class UsageLoggingCallbackHandler(BaseCallbackHandler):
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self._start_times: dict[Any, float] = {}

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        self._start_times[run_id] = perf_counter()

    def on_llm_end(
        self,
        response: LangChainResult,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        start_time = self._start_times.pop(run_id, None)
        latency_ms = (
            int((perf_counter() - start_time) * 1000)
            if start_time is not None
            else 0
        )

        message = None
        if response.generations and response.generations[0]:
            message = getattr(response.generations[0][0], "message", None)

        model = settings.llm_model_id
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0

        if message is not None:
            metadata = message.response_metadata or {}
            model = metadata.get("model_name") or model
            usage = message.usage_metadata or {}
            input_tokens = usage.get("input_tokens") or 0
            output_tokens = usage.get("output_tokens") or 0
            total_tokens = usage.get("total_tokens") or 0
            if not total_tokens:
                raw = metadata.get("token_usage") or {}
                input_tokens = (
                    input_tokens or raw.get("prompt_tokens") or 0
                )
                output_tokens = (
                    output_tokens or raw.get("completion_tokens") or 0
                )
                total_tokens = (
                    raw.get("total_tokens")
                    or input_tokens + output_tokens
                )

        record = {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
        }
        self.records.append(record)
        logger.info(
            "llm usage model=%s input_tokens=%s output_tokens=%s "
            "total_tokens=%s latency_ms=%s",
            model,
            input_tokens,
            output_tokens,
            total_tokens,
            latency_ms,
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        self._start_times.pop(run_id, None)
        logger.warning(
            "llm error type=%s error=%s",
            type(error).__name__,
            error,
        )

    def summary(self) -> dict[str, int]:
        return {
            "calls": len(self.records),
            "input_tokens": sum(
                record["input_tokens"] for record in self.records
            ),
            "output_tokens": sum(
                record["output_tokens"] for record in self.records
            ),
            "total_tokens": sum(
                record["total_tokens"] for record in self.records
            ),
        }


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
) -> Decimal | None:
    """按配置单价估算成本；未配置单价时返回 None。"""
    input_price = settings.LLM_INPUT_PRICE_PER_MILLION
    output_price = settings.LLM_OUTPUT_PRICE_PER_MILLION
    if input_price is None or output_price is None:
        return None
    return (
        Decimal(input_tokens) / Decimal(1_000_000) * input_price
        + Decimal(output_tokens) / Decimal(1_000_000) * output_price
    )
