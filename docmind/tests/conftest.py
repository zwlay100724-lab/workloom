"""DocMind 测试环境。

必须在 import app.* / shared.* 之前设置环境变量：
- shared.db.database 在 import 时读取 DATABASE_URL 并建 engine；
- app.core.config 的 Settings 在 import 时实例化。

用 setdefault 而不是直接赋值：本地已导出的环境变量优先，
但默认值保证测试不会去连真实的 Postgres / 调用真实 LLM。
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("LLM_API_KEY", "test-only-llm-key")
os.environ.setdefault("EMBED_API_KEY", "test-only-embed-key")
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-only-jwt-secret-0123456789abcdef",
)
