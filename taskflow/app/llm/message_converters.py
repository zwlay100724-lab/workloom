"""
app/llm/message_converters.py
消息格式转换工具：数据库 Message ↔ LangChain BaseMessage

注意：本项目 Message 是 SQLAlchemy 模型（models/conversation.py），
字段只有 role + content 两列，存不下 tool_calls 这类结构化信息。
所以这里只支持普通角色；tool 消息无法往返。
"""
from app.models.conversation import Message
from app.services.conversation_service import ConversationService
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)

# 数据库 Message.role 是字符串，映射到对应的 LangChain 消息类。
_ROLE_TO_MESSAGE: dict[str, type[BaseMessage]] = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


def db_message_to_langchain(db_message: Message) -> BaseMessage:
    """数据库 Message → LangChain BaseMessage。

    未知角色直接抛 ValueError，而不是静默当作 HumanMessage：
    角色打错字（如 "asistant"）若被当作用户消息喂回模型，
    会把助手回答变成用户输入，整段对话就乱了。
    """
    message_cls = _ROLE_TO_MESSAGE.get(db_message.role)
    if message_cls is None:
        raise ValueError(
            f"Unsupported role for LangChain: {db_message.role!r}"
        )
    return message_cls(content=db_message.content)


def langchain_message_to_db(msg: BaseMessage) -> dict[str, str]:
    """LangChain BaseMessage → 写入 Message 表所需的 role/content。

    只返回 role + content 两个键，因为 Message 表只有这两列。
    用法：
        data = langchain_message_to_db(msg)
        await conversation_service.add_message(
            conversation_id=...,
            role=data["role"],
            content=data["content"],
        )

    ToolMessage 抛 ValueError：tool 调用需要 tool_call_id，
    而 Message 表没有这一列，硬存会丢信息、无法往返。
    将来给 Message 表加了 tool_call_id / tool_calls 列后，再来补这个分支。
    """
    role_map = {
        "human": "user",
        "ai": "assistant",
        "system": "system",
    }
    role = role_map.get(msg.type)
    if role is None:
        raise ValueError(
            f"Cannot store message type to DB: {msg.type!r} "
            "(Message 表只有 role + content，存不下 tool 消息)"
        )
    return {
        "role": role,
        "content": msg.content or "",
    }


def truncate_history_by_tokens(
    messages: list[BaseMessage],
    max_tokens: int = 3000,
) -> list[BaseMessage]:
    """按 Token 预算截断历史，只保留"最新的、放得下的"一段。

    实现思路：从最新一条往回累加 Token 数，超过预算就停止，
    丢弃更早的消息；返回仍是正序（最早在前）。

    为什么"最新一条即使超预算也要保留"：
    如果最新一条本身就比预算大，全截掉会得到空历史——
    空历史让模型丢掉最近的上下文，比超预算更糟。
    所以用 selected 是否为空来判断，保证至少保住最相关的那条。

    Token 估算(len // 2)是粗略的：中文约 1.5 字/token、
    英文约 4 字符/token，len//2 相当于按 2 字符/token 估，
    两种语言都偏保守（偏高）。宁可多截一点，也不要超模型上下文。
    生产环境建议换 tiktoken 或模型对应的 tokenizer。
    """

    def estimate_tokens(text: str) -> int:
        return len(text) // 2

    selected: list[BaseMessage] = []
    total_tokens = 0

    # messages 是正序(最早在前)，reversed 后即"最新在前"，从最新往回挑。
    for msg in reversed(messages):
        content = (
            msg.content
            if isinstance(msg.content, str)
            else str(msg.content)
        )
        msg_tokens = estimate_tokens(content)
        # 已经保住至少一条之后才考虑"放不放得下"，
        # 避免"最新一条本身超预算"时得到空历史。
        if selected and total_tokens + msg_tokens > max_tokens:
            break
        selected.append(msg)
        total_tokens += msg_tokens

    # 反转回正序
    return list(reversed(selected))


async def load_history_as_langchain_messages(
    conversation_service: ConversationService,
    conversation_id: int,
    limit: int = 20,
    max_tokens: int | None = None,
) -> list[BaseMessage]:
    """从数据库加载对话历史，转成 LangChain 消息列表（正序，最早的在最前）。

    注意：list_recent_messages 内部已经是
    order_by(created_at desc) 再 reverse，返回的就是正序，
    所以这里不要再反转（否则历史就倒过来了）。

    limit 默认 20 条 = 10 轮对话：太少丢上下文，太多占 Token。
    max_tokens 非 None 时，再按 Token 预算截断一次。
    limit 是"最多取多少条"，max_tokens 是"总 Token 不超过多少"，
    两者叠加：先按条数粗筛、再按 Token 精筛。
    """
    db_messages = await conversation_service.list_recent_messages(
        conversation_id=conversation_id,
        limit=limit,
    )
    messages = [
        db_message_to_langchain(m)
        for m in db_messages
    ]
    if max_tokens is not None:
        messages = truncate_history_by_tokens(
            messages,
            max_tokens=max_tokens,
        )
    return messages