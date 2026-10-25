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


settings = Settings()