"""Embedding 封装：复用 langchain_community 对百炼（DashScope）的专门适配类。"""
from functools import lru_cache

from langchain_community.embeddings import DashScopeEmbeddings

from app.core.config import settings


@lru_cache(maxsize=1)
def get_embeddings() -> DashScopeEmbeddings:
    """返回 DashScope（百炼）embedding 实例。

    - 用 langchain_community.embeddings.DashScopeEmbeddings：走 DashScope 原生 API
      （内部依赖 `dashscope` 包，需 pip install dashscope），不经过 OpenAI 兼容层。
    - model / api_key 取 settings（EMBED_MODEL_ID / EMBED_API_KEY）。
    - 该适配类已按 model 处理 batch 上限，检索时自动区分 text_type=document / query，
      不依赖 EMBED_BASE_URL（原生接口固定走 dashscope.aliyuncs.com）。

    缓存：与 get_chat_model() 对齐。图里每个 retrieve 节点都会构造一次检索服务，
    不缓存的话每次调用都要重新 new 一个 embedding 客户端。
    """
    return DashScopeEmbeddings(
        model=settings.EMBED_MODEL_ID,
        dashscope_api_key=settings.EMBED_API_KEY,
    )