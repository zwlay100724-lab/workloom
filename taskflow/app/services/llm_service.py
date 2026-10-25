import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from time import perf_counter
from typing import Annotated

from app.core.config import (
    llm_input_price_per_million,
    llm_output_price_per_million,
    settings,
)
from app.llm.callbacks import UsageTrackingCallbackHandler
from app.llm.chains import (
    get_chat_chain,
    get_task_intent_chain,
    get_task_intent_structured_chain,
)
from app.llm.client import LLMResult, get_chat_model, llm_client
from app.llm.message_converters import load_history_as_langchain_messages
from app.llm.prompts import (
    TASK_PLANNING_SYSTEM_PROMPT,
    TASKFLOW_TOOL_SYSTEM_PROMPT,
    build_task_extraction_system_prompt,
)
from app.llm.tools import build_task_tools
from app.models.llm_usage import LLMUsage
from app.repositories.llm_usage_repository import llm_usage_repository
from app.schemas.ai import (
    StructuredTaskData,
    TaskIntent,
    TaskPlan,
)
from app.services.conversation_service import (
    ConversationService,
    get_conversation_service,
)
from fastapi import Depends
from fastapi.concurrency import run_in_threadpool
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from pydantic import ValidationError
from shared.db.database import get_session
from shared.exceptions import LLMStructuredOutputError
from sqlmodel import Session

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# 1. LangChain 辅助函数:用量读取
#    消息转换已迁到 app/llm/message_converters.py;
#    用量回调(UsageTrackingCallbackHandler)在 app/llm/callbacks.py
# ═══════════════════════════════════════════════════════════════════════


def _extract_usage(
    response: AIMessage,
) -> tuple[str, int | None, int | None, int | None]:
    """从 LangChain AIMessage 中取出 model 名与 token 用量。

    只用于"单次调用、最终输出是 AIMessage"的路径
    (chat / stream_chat / chat_with_history)。
    多调用流程(工具循环 / 意图重试)统一走 UsageTrackingCallbackHandler。
    """
    usage_metadata = response.usage_metadata or {}
    model = (
        response.response_metadata.get("model_name")
        or settings.llm_model_id
    )
    return (
        model,
        usage_metadata.get("input_tokens"),
        usage_metadata.get("output_tokens"),
        usage_metadata.get("total_tokens"),
    )


# ═══════════════════════════════════════════════════════════════════════
# 2. 用量 / 费用落库
# ═══════════════════════════════════════════════════════════════════════


def calculate_cost(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    input_price: Decimal | None,
    output_price: Decimal | None,
) -> Decimal | None:

    if (
        input_tokens is None
        or output_tokens is None
        or input_price is None
        or output_price is None
    ):
        return None
    return (
        Decimal(input_tokens) / Decimal(1_000_000) * input_price
        +
        Decimal(output_tokens) / Decimal(1_000_000) * output_price
    )


def _record_usage(
    *,
    session: Session,
    user_id: int,
    conversation_id: int | None,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    latency_ms: int,
) -> LLMUsage:
    """把一次 LLM 调用的用量 / 延迟 / 费用统一落库。

    LangChain 路径(chat / stream_chat / chat_with_history)
    与老的 llm_client 路径(generate)共用。
    """
    # 成本仍按原始 usage 计算：provider 没给 token 时成本保持 None，
    # 而不是错误地估算成 0。
    estimated_cost = calculate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_price=llm_input_price_per_million,
        output_price=llm_output_price_per_million,
    )

    # LLMUsage 的三列是 NOT NULL；provider 没返回 usage 时落 0，
    # 不能让 NULL 触发 IntegrityError 把一次成功的回答变成 500。
    normalized_input = input_tokens or 0
    normalized_output = output_tokens or 0
    normalized_total = total_tokens or (
        normalized_input + normalized_output
    )

    usage = LLMUsage(
        user_id=user_id,
        conversation_id=conversation_id,
        model=model,
        input_tokens=normalized_input,
        output_tokens=normalized_output,
        total_tokens=normalized_total,
        latency_ms=latency_ms,
        estimated_cost_usd=estimated_cost,
    )

    return llm_usage_repository.create(
        session=session,
        usage=usage,
    )


def _record_usage_with_new_session(bind, **kwargs) -> None:
    """流式请求的收尾记账。

    客户端断开时请求级 Session 可能已经被依赖清理关闭；这里用 bind
    新开一个 Session，避免记账和清理互相竞争。
    """
    with Session(bind) as session:
        _record_usage(session=session, **kwargs)


# ═══════════════════════════════════════════════════════════════════════
# 3. LLMService:所有 LLM 能力的统一入口
#    聊天 / 流式 / 结构化输出 / 工具调用 / 意图解析
# ═══════════════════════════════════════════════════════════════════════


class LLMService:
    """所有 LLM 能力的统一入口:聊天、流式、结构化输出、工具调用、意图解析。"""

    def __init__(
        self,
        conversation_service: ConversationService,
        session: Session,
    ) -> None:
        self.conversation_service = conversation_service
        self.session = session

    def _record_usage_from_handler(
        self,
        handler: UsageTrackingCallbackHandler,
    ) -> None:
        """把 UsageTrackingCallbackHandler 收集的 records 统一落库。

        一个 handler 对应一次请求里可能发生的多次 LLM 调用
        (工具循环 / 意图重试),每条 record 落一条 LLMUsage。
        成功 / 失败的尝试都要记——模型都真实消耗了 token。
        """
        for record in handler.get_records():
            _record_usage(
                session=self.session,
                user_id=record["user_id"],
                conversation_id=record["conversation_id"],
                model=record["model"],
                input_tokens=record["input_tokens"],
                output_tokens=record["output_tokens"],
                total_tokens=record["total_tokens"],
                latency_ms=record["latency_ms"],
            )

    async def _record_handler_usage(
        self,
        handler: UsageTrackingCallbackHandler,
    ) -> None:
        """把回调记录切到线程池落库，避免同步 commit 阻塞事件循环。"""
        try:
            await run_in_threadpool(
                self._record_usage_from_handler,
                handler,
            )
        except Exception:
            # 记账失败不应该改变已经完成的回答/意图解析结果。
            logger.exception("failed to record LLM usage")

    async def chat(
        self,
        *,
        message: str,
        user_id: int,
    ) -> str:
        """单轮普通聊天:LangChain 调用,返回纯文本。"""
        started = perf_counter()
        chain = get_chat_chain()

        response = await chain.ainvoke(
            {
                "history": [],
                "user_input": message,
            }
        )
        content = response.content or ""

        latency_ms = int(
            (perf_counter() - started) * 1000
        )
        model_name, input_tokens, output_tokens, total_tokens = (
            _extract_usage(response)
        )

        await run_in_threadpool(
            _record_usage,
            session=self.session,
            user_id=user_id,
            conversation_id=None,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

        return content

    async def stream_chat(
        self,
        *,
        message: str,
        user_id: int,
    ) -> AsyncIterator[str]:
        """流式普通聊天:LangChain astream 逐字返回。

        记账放在 finally 里：
        - 正常结束要记；
        - 客户端断开导致生成器取消时也要记；
        - 流中断也要把已经发生的调用记下来。
        使用独立 Session + asyncio.shield，避免请求级 Session 被清理后
        记账任务跟着失效。
        """
        started = perf_counter()
        chain = get_chat_chain()
        usage_metadata: dict = {}
        bind = self.session.get_bind()

        try:
            # stream_usage=True 已经在 get_chat_chain() 里用 bind 焊死在模型上了，
            # 所以这里不需要也不能再传给链的 astream(实测传了也会被吞掉)。
            async for chunk in chain.astream(
                {
                    "history": [],
                    "user_input": message,
                }
            ):
                content = chunk.content
                if content:
                    yield content
                # 只有最后一个空 choices 的 chunk 才带 usage_metadata
                if chunk.usage_metadata:
                    usage_metadata = chunk.usage_metadata
        finally:
            latency_ms = int(
                (perf_counter() - started) * 1000
            )

            try:
                # shield：即使当前任务因为客户端断开被取消，
                # 记账线程仍会继续完成。
                await asyncio.shield(
                    run_in_threadpool(
                        _record_usage_with_new_session,
                        bind,
                        user_id=user_id,
                        conversation_id=None,
                        model=settings.llm_model_id,
                        input_tokens=usage_metadata.get("input_tokens"),
                        output_tokens=usage_metadata.get("output_tokens"),
                        total_tokens=usage_metadata.get("total_tokens"),
                        latency_ms=latency_ms,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # 记账失败不能影响已经发出的流内容。
                logger.exception("failed to record stream usage")

    async def chat_with_history(
        self,
        *,
        conversation_id: int,
        user_id: int,
        message: str,
        history_max_tokens: int = 3000,
    ) -> str:
        """带历史的多轮普通聊天:LangChain 调用,并保存 user/assistant 消息。

        history_max_tokens:历史 Token 预算。limit 先按条数粗筛
        (最多取 20 条),再按这个预算把更早的、放不下的消息截掉。
        """
        await self.conversation_service.get_owned_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
        )

        # MessagesPlaceholder 只接受 BaseMessage 列表。
        # 加载 + 转换 + Token 截断统一走 message_converters 模块。
        history_messages = await load_history_as_langchain_messages(
            conversation_service=self.conversation_service,
            conversation_id=conversation_id,
            max_tokens=history_max_tokens,
        )

        # 注意顺序:必须先读历史、再写入当前 user 消息。
        # 如果先 add_message 再读历史,本次输入会被算进 history,
        # 和下面传给链的 user_input 参数重复,模型会看到两遍当前消息。
        await self.conversation_service.add_message(
            conversation_id=conversation_id,
            role="user",
            content=message,
        )

        started = perf_counter()
        chain = get_chat_chain()
        response = await chain.ainvoke(
            {
                "history": history_messages,
                "user_input": message,
            }
        )
        content = response.content or ""

        latency_ms = int(
            (perf_counter() - started) * 1000
        )
        model_name, input_tokens, output_tokens, total_tokens = (
            _extract_usage(response)
        )

        await run_in_threadpool(
            _record_usage,
            session=self.session,
            user_id=user_id,
            conversation_id=conversation_id,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

        await self.conversation_service.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
        )

        return content

    async def extract_task(
        self,
        *,
        message: str,
        user_id: int,
    ) -> tuple[StructuredTaskData, LLMUsage]:
        """把用户消息抽取为结构化任务数据。

        结构化输出走老的 llm_client 路径(generate),自动记账。
        """
        messages = [
            {
                "role": "system",
                "content": build_task_extraction_system_prompt(
                    date.today()
                ),
            },
            {
                "role": "user",
                "content": message,
            },
        ]

        # LLM 偶发返回空/截断/非法 JSON。第一轮用 json_object 强制 JSON;
        # 若失败,第二轮退化为不指定 format(系统 prompt 已要求返回 JSON)。
        last_error: Exception | None = None

        for attempt in range(2):
            result, usage = await generate(
                messages=messages,
                user_id=user_id,
                session=self.session,
                temperature=0,
                max_tokens=500,
                response_format=(
                    {"type": "json_object"}
                    if attempt == 0
                    else None
                ),
            )

            try:
                task_data = StructuredTaskData.model_validate_json(
                    result.content
                )
                return task_data, usage
            except ValidationError as exc:
                last_error = exc
                # 继续重试(下一轮不带 response_format)

        raise LLMStructuredOutputError(
            "LLM returned invalid structured data"
        ) from last_error

    async def plan_tasks(
        self,
        *,
        message: str,
        user_id: int,
    ) -> TaskPlan:
        """只负责生成计划。

        重要:这个函数绝对不调用 task_service.create_task()。
        因为当前阶段是 Plan,不是 Execute。
        """
        messages = [
            {
                "role": "system",
                "content": TASK_PLANNING_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": message,
            },
        ]
        # 继续走前面统一封装好的 generate(),
        # 这样 Token、Latency、Usage 仍然会被记录。
        result, _usage = await generate(
            messages=messages,
            user_id=user_id,
            session=self.session,
            temperature=0.2,
            response_format={
                "type": "json_object",
            },
        )
        # LLM 输出仍然是不可信数据。
        # 必须再次经过 Pydantic 校验。
        plan = TaskPlan.model_validate_json(
            result.content
        )
        return plan

    async def chat_with_tools(
        self,
        *,
        message: str,
        user_id: int,
        max_rounds: int = 5,
    ) -> str:
        """带工具调用的多轮对话(LangChain ChatModel + StructuredTool)。

        流程:模型绑定 build_task_tools 返回的工具 → 调用模型 →
        若返回 tool_calls 就逐个执行并回传 ToolMessage → 再调用模型,
        直到模型不再要求调用工具。

        记账:整轮循环共用一个 UsageTrackingCallbackHandler,
        所有轮次的 LLM 调用都收进 handler,结束后在 finally 里统一落库。
        """
        tools = build_task_tools(
            session=self.session,
            owner_id=user_id,
        )
        tool_by_name = {
            tool.name: tool
            for tool in tools
        }
        model = get_chat_model().bind_tools(tools)

        messages: list[BaseMessage] = [
            SystemMessage(content=TASKFLOW_TOOL_SYSTEM_PROMPT),
            HumanMessage(content=message),
        ]

        handler = UsageTrackingCallbackHandler(user_id=user_id)

        try:
            for _round in range(max_rounds):
                response = await model.ainvoke(
                    messages,
                    config={"callbacks": [handler]},
                )

                # 模型没要求调工具:这就是最终回答
                if not response.tool_calls:
                    return response.content or ""

                # 把模型这次的 tool_calls 原样拼回对话历史,
                # 再逐个执行工具,把结果作为 ToolMessage 按 tool_call_id 配对回传。
                messages.append(response)

                for tool_call in response.tool_calls:
                    tool = tool_by_name.get(tool_call["name"])
                    if tool is None:
                        result = {
                            "success": False,
                            "error": f"Unknown tool: {tool_call['name']}",
                        }
                    else:
                        # StructuredTool 会先按 args_schema 校验;
                        # 校验失败/业务异常在工具内部已转成错误 dict。
                        # 这里再兜底一层,任何意料之外的执行错误
                        # 都转成错误信息喂回模型,而不是让整轮对话崩掉。
                        try:
                            result = await tool.ainvoke(tool_call["args"])
                        except Exception as exc:
                            result = {
                                "success": False,
                                "error": f"tool execution failed: {exc}",
                            }
                    messages.append(
                        ToolMessage(
                            content=json.dumps(
                                result,
                                ensure_ascii=False,
                                default=str,
                            ),
                            tool_call_id=tool_call["id"],
                        )
                    )

            # 保险丝:模型一直要求调工具(比如 prompt 失控)时防止死循环
            raise RuntimeError(
                f"tool loop exceeded {max_rounds} rounds"
            )
        finally:
            # 无论正常返回、熔断还是异常,已发生的 LLM 调用都要记账。
            # 放到线程池，避免同步 commit 阻塞事件循环。
            await self._record_handler_usage(handler)

    async def _run_intent_chain(
        self,
        chain: Runnable,
        *,
        user_input: str,
        user_id: int,
    ) -> TaskIntent:
        """对"意图解析链"做最多 2 次重试,每次尝试都记账。

        PydanticOutputParser 版和 with_structured_output 版共用——
        两条链的输出都是 TaskIntent,只是构造方式不同。

        为什么捕获 (OutputParserException, ValueError):
        - Parser 版解析失败抛 OutputParserException;
        - 结构化版(json_schema 方法)解析失败抛 ValueError。
        """
        last_error: Exception | None = None

        for _attempt in range(2):
            handler = UsageTrackingCallbackHandler(user_id=user_id)

            try:
                # 链最后一层要么是 Parser、要么是 with_structured_output 的解析器,
                # 所以 result 已经是 TaskIntent,不再是 AIMessage。
                result = await chain.ainvoke(
                    {"user_input": user_input},
                    # 回调随 config 穿透整条链,在模型结束调用时抓 usage
                    config={"callbacks": [handler]},
                )
            except (OutputParserException, ValueError) as exc:
                # 模型输出了无法解析的结果,换一轮重试。
                # 但模型已经真实消耗了 token,失败的尝试也要记账。
                last_error = exc
                await self._record_handler_usage(handler)
                continue

            await self._record_handler_usage(handler)
            return result

        raise LLMStructuredOutputError(
            "LLM returned invalid structured data"
        ) from last_error

    async def parse_task_intent(
        self,
        *,
        user_input: str,
        user_id: int,
    ) -> TaskIntent:
        """PydanticOutputParser 版意图解析:prompt 教格式 + 本地解析。"""
        return await self._run_intent_chain(
            get_task_intent_chain(),
            user_input=user_input,
            user_id=user_id,
        )

    async def parse_task_intent_structured(
        self,
        *,
        user_input: str,
        user_id: int,
    ) -> TaskIntent:
        """with_structured_output() 版意图解析:结构由模型 API 保证。"""
        return await self._run_intent_chain(
            get_task_intent_structured_chain(),
            user_input=user_input,
            user_id=user_id,
        )

# ═══════════════════════════════════════════════════════════════════════
# 4. DI 注入:Router 通过 Depends 拿到 LLMService
# ═══════════════════════════════════════════════════════════════════════


def get_llm_service(
    conversation_service: Annotated[
        ConversationService,
        Depends(get_conversation_service),
    ],
    session: Annotated[
        Session,
        Depends(get_session),
    ],
) -> LLMService:
    return LLMService(
        conversation_service,
        session,
    )


LLMServiceDep = Annotated[
    LLMService,
    Depends(get_llm_service),
]

# ═══════════════════════════════════════════════════════════════════════
# 5. 老的 llm_client 统一封装(generate)
# 结构化输出、工具调用、任务规划仍走这条路,自动记账。
# ═══════════════════════════════════════════════════════════════════════


async def generate(
    *,
    messages: list[dict],
    user_id: int,
    session: Session,
    conversation_id: int | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1000,
    response_format: dict | None = None,
    tools=None,
    tool_choice=None,
) -> tuple[LLMResult, LLMUsage]:

    started = perf_counter()
    # 真正的模型调用
    result = await llm_client.chat(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        tools=tools,
        tool_choice=tool_choice,
    )

    latency_ms = int(
        (perf_counter() - started) * 1000
    )

    usage = await run_in_threadpool(
        _record_usage,
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        latency_ms=latency_ms,
    )
    return result, usage


# ═══════════════════════════════════════════════════════════════════════
# 6~8. 结构化输出 / 工具调用 / 意图解析
# 这些能力的公开入口已收进 LLMService 类(见上方类定义),
# 模块级只保留 generate 这一个底层 llm_client 封装。
# ═══════════════════════════════════════════════════════════════════════
