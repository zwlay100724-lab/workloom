"""统一的日志格式。

TaskFlow / DocMind 的 app 入口和 eval 脚本都调用它，保证日志字段一致：
时间 | 级别 | logger 名 | 消息（消息内部使用 key=value）。
"""
import logging

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT)
    # OpenAI SDK 底层用 httpx；每个请求都打 INFO 太吵，只保留警告以上。
    logging.getLogger("httpx").setLevel(logging.WARNING)
