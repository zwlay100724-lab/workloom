"""跨项目共享的配置基类。

【为什么叫 BaseConfig,不叫 Settings、也没有 settings 单例?】
Settings 属于每个 app 自己:
- taskflow/app/core/config.py  继承它 + 加 jwt_secret_key / 记账单价
- docmind/app/core/config.py   继承它 + 加 embedding_model_id / chunk 参数
shared 只定义"两个 app 一定都会用到的公共字段",
绝不在模块级 new 一份 settings——那会让 taskflow 和 docmind 共用同一个配置对象。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseConfig(BaseSettings):
    """所有业务 app 共用的配置字段,字段名与 .env 里的环境变量一致。

    注意这里只放"每个 app 都必须有"的字段;
    某个 app 独有的(如 jwt_secret_key)应放在它自己的 Settings 子类里,
    否则别的 app 会被迫配一个用不上的环境变量。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    database_url: str
    redis_url: str

    # LLM 配置(OpenAI 兼容接口,目前指向 DeepSeek)
    llm_api_key: str
    llm_model_id: str = "deepseek-v4-pro"
    llm_base_url: str = "https://api.deepseek.com"

    # 单次 LLM 请求最多等待多少秒
    llm_timeout_seconds: float = 30.0

    # 最大重试次数
    # 注意:这里指额外重试次数,不是总请求数。
    llm_max_retries: int = 2

    # 第一次退避等待时间
    llm_retry_base_delay: float = 0.5