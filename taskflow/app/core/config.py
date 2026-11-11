from decimal import Decimal

from shared.config import BaseConfig

# TaskFlow 记账用的单价(USD / 百万 token)。
# 当前默认 None(不计费)。这是业务量,所以留在 app,不进 shared。
llm_input_price_per_million: Decimal | None = None
llm_output_price_per_million: Decimal | None = None


class Settings(BaseConfig):
    """TaskFlow 的配置:继承 shared.BaseConfig 的公共字段,只加本 app 特有字段。

    字段名与 .env 一致(如 LLM_API_KEY、JWT_SECRET_KEY)。
    注意 jwt_secret_key 放在这里而不是 shared——DocMind 不用登录,不该被迫配它。
    """

    jwt_secret_key: str

    # 跨服务调用：TaskFlow → DocMind 的纯检索接口。
    # compose 里指向服务名 api-docmind；本地直跑默认本机 8001。
    docmind_base_url: str = "http://localhost:8001"
    docmind_timeout_seconds: float = 10.0

    # DocMind 发布文档生命周期事件到它自己的 Redis 队列；
    # TaskFlow 的事件 worker 连同一个实例消费。
    doc_event_redis_url: str = "redis://localhost:6380/0"
    doc_event_queue_name: str = "workloom:events:taskflow"
    doc_event_max_attempts: int = 3
    doc_event_retry_base_delay: float = 5.0


settings = Settings()