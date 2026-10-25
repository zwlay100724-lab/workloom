"""共享的 LLM 基础设施。

shared.llm.client 是"OpenAI 兼容接口的通用封装"(重试 / 超时 / 异常归一),
与具体业务无关。它需要 config,但 config 由 app 注入(见 shared/__init__.py 约定)。
"""