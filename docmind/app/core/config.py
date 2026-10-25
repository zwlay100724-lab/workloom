"""DocMind 的配置:继承 shared.BaseConfig,只声明本 app 特有的字段。

为什么必须在 app 里 new settings 单例、shared 里只有 BaseConfig?
因为"要往配置里加哪些产品字段"只有 app 自己知道
(shared 不能知道 docmind 关心 chunk_size / top_k)。
shared/__init__.py 的原则就是:shared 只装纯组件,不持有任何 app 的配置实例。
"""
from decimal import Decimal

from shared.config import BaseConfig


class Settings(BaseConfig):
    """DocMind 的 Settings 子类,字段名与 .env 里的环境变量一致。

    下面这些是 DocMind 特有字段,先给占位默认值,后续随方案调整:
    - embedding_model_id:向量化模型。注意 DeepSeek 官方目前不提供 embedding API,
      这里先占位,真正选型(本地 sentence-transformers / 别家兼容 endpoint)待定。
    - chunk_size / chunk_overlap:文档切块参数,要按实际文档集调。
    - top_k:检索时取回多少个块拼进 prompt。
    """

    EMBED_MODEL_ID: str = "text-embedding-v4"  # 你的 Embedding 模型名
    EMBED_API_KEY: str = ""  # 通常和 LLM API Key 相同
    EMBED_BASE_URL: str = ""  # 通常和 LLM Base URL 相同
    EMBED_DIMENSION: int = 1024  # 模型输出维度，建表时要用
    # 声明 EMBED_MODEL_TYPE 是为了匹配 .env 里的键：pydantic-settings 默认
    # extra=forbid，.env 有而 Settings 没声明的键会在实例化时直接报错。
    # 当前代码还没用它（选型见 embeddings.py），先占位保留。
    EMBED_MODEL_TYPE: str = ""  # embedding 提供方（dashscope / ollama / ...）

    # DocMind 只验签、不签发 token。必须和 TaskFlow 使用同一个 secret。
    JWT_SECRET_KEY: str

    # 可选：LLM 单价（USD / 百万 token）。不配置时只记录 token，不估算成本。
    LLM_INPUT_PRICE_PER_MILLION: Decimal | None = None
    LLM_OUTPUT_PRICE_PER_MILLION: Decimal | None = None

    CHUNK_SIZE: int = 800
    CHUNK_OVERLAP: int = 80
    TOP_K: int = 5


settings = Settings()