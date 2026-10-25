"""
app/llm/callbacks.py
自定义 LangChain CallbackHandler
职责：在 LLM 调用的各个生命周期节点收集统计信息，供 Service 层统一写入 LLMUsage 表

【为什么单独建一个文件？】
Callback 逻辑和业务逻辑是不同的关注点：
- llm_service.py 管"怎么执行"
- tools.py 管"能做什么"
- callbacks.py 管"执行过程中发生了什么"
分开后代码更清晰，也方便以后扩展更多监控逻辑。
"""
from time import perf_counter
from typing import Any

from app.core.config import settings
from langchain_core.callbacks import BaseCallbackHandler

# 注意：LangChain 也有一个 LLMResult(在 langchain_core.outputs)。
# 为避免和项目自己的 app.llm.client.LLMResult 撞名，这里用别名区分(与 llm_service 一致)。
from langchain_core.outputs import LLMResult as LangChainResult


class UsageTrackingCallbackHandler(BaseCallbackHandler):
    """Token 用量 + 延迟 + 错误追踪的 Callback Handler。

    一个 Handler 实例对应一次完整的请求（可能包含多次 LLM 调用）。
    每次 on_llm_start 记录开始时间，每次 on_llm_end / on_llm_error
    计算统计并存进 self.records。

    records 的字段和 LLMUsage 表对齐（input_tokens / output_tokens / ...），
    Service 层拿到 records 后可以直接喂给 _record_usage() 落库。例如：

        handler = UsageTrackingCallbackHandler(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        # ... 用 config={"callbacks": [handler]} 跑链或 agent ...
        for record in handler.get_records():
            _record_usage(
                session=session,
                user_id=record["user_id"],
                conversation_id=record["conversation_id"],
                model=record["model"],
                input_tokens=record["input_tokens"],
                output_tokens=record["output_tokens"],
                total_tokens=record["total_tokens"],
                latency_ms=record["latency_ms"],
            )

    success / error_message 只用于内存里判断本次请求是否失败，
    LLMUsage 表没有这两列，不会落库。

    【为什么不在 Handler 里直接写数据库？】
    Handler 是 LangChain 内部调用的，不应该依赖数据库 Session。
    先收集到 self.records，等请求执行完由 Service 层统一写库（便于测试）。
    """

    def __init__(
        self,
        user_id: int,
        conversation_id: int | None = None,
    ) -> None:
        """参数：user_id(来自 JWT，创建 Handler 时注入)；conversation_id(可选)。"""
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.records: list[dict[str, Any]] = []
        # run_id → 开始时间。每个 LLM 调用一个 run，用 run_id 匹配 start/end。
        self._start_times: dict[Any, float] = {}

    # ----------------------------------------------------------
    # LLM 调用开始
    # ----------------------------------------------------------
    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """LLM 开始调用时触发，记录开始时间。"""
        # 用 perf_counter 而非 time.time：单调时钟，不受系统时间跳变影响。
        self._start_times[run_id] = perf_counter()

    # ----------------------------------------------------------
    # LLM 调用成功结束
    # ----------------------------------------------------------
    def on_llm_end(
        self,
        response: LangChainResult,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """LLM 调用成功结束时触发，提取 Token 用量 / 模型名 / 延迟。"""
        # 正常流程 on_llm_start 一定先于 on_llm_end；
        # 拿不到 start 属异常，兜底记 0，而不是把"当前时间"冒充开始时间。
        start_time = self._start_times.pop(run_id, None)
        latency_ms = (
            int((perf_counter() - start_time) * 1000)
            if start_time is not None
            else 0
        )

        message = None
        if response.generations and response.generations[0]:
            message = response.generations[0][0].message

        # 默认用配置里的模型名兜底，避免落空字符串(LLMUsage.model 非空约束)。
        model = settings.llm_model_id
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0

        if message is not None:
            metadata = message.response_metadata or {}
            model = (
                metadata.get("model_name")
                or settings.llm_model_id
            )

            # 优先读 LangChain 标准化的 usage_metadata(和 _extract_usage 一致)；
            # 个别 provider 只给 response_metadata 里的原始 token_usage，做兜底。
            usage = message.usage_metadata or {}
            input_tokens = usage.get("input_tokens") or 0
            output_tokens = usage.get("output_tokens") or 0
            total_tokens = usage.get("total_tokens") or 0
            if not total_tokens:
                raw = metadata.get("token_usage") or {}
                input_tokens = input_tokens or raw.get("prompt_tokens") or 0
                output_tokens = output_tokens or raw.get("completion_tokens") or 0
                total_tokens = (
                    raw.get("total_tokens")
                    or input_tokens + output_tokens
                )

        self.records.append({
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
            "success": True,
            "error_message": None,
        })

    # ----------------------------------------------------------
    # LLM 调用出错
    # ----------------------------------------------------------
    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """LLM 调用出错时触发。失败也要记一条——模型可能已真实消耗了 token。"""
        start_time = self._start_times.pop(run_id, None)
        latency_ms = (
            int((perf_counter() - start_time) * 1000)
            if start_time is not None
            else 0
        )

        # 错误信息只取前 500 字符，避免日志/后续处理过长。
        error_msg = f"{type(error).__name__}: {error}"[:500]

        self.records.append({
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            # 出错时拿不到模型名，用配置兜底。
            "model": settings.llm_model_id,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency_ms": latency_ms,
            "success": False,
            "error_message": error_msg,
        })

    # ----------------------------------------------------------
    # 汇总方法：供 Service 层调用
    # ----------------------------------------------------------
    def get_records(self) -> list[dict[str, Any]]:
        """返回所有收集到的统计记录。"""
        return self.records

    def get_total_tokens(self) -> int:
        """本次请求所有 LLM 调用的总 Token 数（方便日志输出）。"""
        return sum(r["total_tokens"] for r in self.records)

    def has_errors(self) -> bool:
        """本次请求是否有 LLM 调用失败。"""
        return any(not r["success"] for r in self.records)