"""查询 Embedding 进程内缓存的测试。"""
from app.rag import embeddings as embeddings_module


class FakeEmbeddings:
    def __init__(self):
        self.calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.1, 0.2]


def test_embed_query_is_cached(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(
        embeddings_module,
        "get_embeddings",
        lambda: fake,
    )
    embeddings_module.clear_embedding_cache()

    try:
        first = embeddings_module.embed_query("same query")
        second = embeddings_module.embed_query("same query")
        embeddings_module.embed_query("other query")

        assert first == [0.1, 0.2]
        assert second == [0.1, 0.2]
        # 相同文本只真正调用一次 Embedding API；不同文本才再调用。
        assert fake.calls == ["same query", "other query"]
    finally:
        embeddings_module.clear_embedding_cache()
