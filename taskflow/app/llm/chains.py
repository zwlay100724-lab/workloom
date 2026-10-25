

from functools import lru_cache

from app.llm.client import get_chat_model
from app.llm.prompts import (
    TASK_INTENT_PROMPT,
    TASK_INTENT_STRUCTURED_PROMPT,
    TASKFLOW_CHAT_PROMPT,
)
from app.schemas.ai import TaskIntent
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import Runnable


@lru_cache(maxsize=1)
def get_chat_chain() -> Runnable:
    """
    TaskFlow 普通聊天 Chain。

    当前仍然保留：
    Prompt -> ChatModel -> AIMessage

    暂时不要改变它的返回类型，
    避免影响你现有 Conversation / LLMUsage 链路。
    """
    model = get_chat_model()
    # 为什么 bind(stream_usage=True)：
    # LCEL 链的 astream() 不会把顶层传入的 kwargs(比如 stream_usage=True)
    # 透传到最里层的 ChatModel(实测收到的是空 dict)。
    # 只有把 stream_usage 通过 bind 焊死在模型上，
    # 流式结束时才能拿到 usage_metadata，用于记录 LLMUsage。
    # 非流式 ainvoke() 不受 bind 影响，照常返回 AIMessage。
    return TASKFLOW_CHAT_PROMPT | model.bind(stream_usage=True)


@lru_cache(maxsize=1)
def get_task_intent_chain() -> Runnable:
    """
    把用户自然语言解析成 TaskIntent。

    数据流：

    user_input
        ↓
    ChatPromptTemplate
        ↓
    ChatModel
        ↓
    AIMessage
        ↓
    PydanticOutputParser
        ↓
    TaskIntent
    """

    model = get_chat_model()

    # Parser 知道最终应该得到 TaskIntent。
    parser = PydanticOutputParser(
        pydantic_object=TaskIntent
    )

    # Parser 根据 TaskIntent 自动生成输出格式说明，
    # 再填入 Prompt 中的 {format_instructions}。
    prompt = TASK_INTENT_PROMPT.partial(
        format_instructions=parser.get_format_instructions()
    )

    # 板块三学过的 LCEL 再次发挥作用。
    return prompt | model | parser


@lru_cache(maxsize=1)
def get_task_intent_structured_chain() -> Runnable:
    """
    with_structured_output() 版意图解析链。

    与 get_task_intent_chain()(PydanticOutputParser)对比：
    - prompt 不再塞 {format_instructions}——输出结构由模型 API 保证；
    - 模型调用时走原生 Structured Output。

    兼容性说明：
    langchain-openai 1.6 里 with_structured_output 默认
    method='json_schema'，依赖模型对
    response_format={'type': 'json_schema', ...} 的原生支持。
    如果 DeepSeek 不接受 json_schema，可改用：
    - method='function_calling'：DeepSeek 支持工具调用，最稳；
    - method='json_mode'：需要换回带 {format_instructions} 的 prompt。
    """

    model = get_chat_model().with_structured_output(
        TaskIntent
    )

    return TASK_INTENT_STRUCTURED_PROMPT | model