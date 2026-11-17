"""「相关知识」缓存 key 的单元测试。"""
from app.repositories.knowledge_cache_repository import build_key


def _key(**overrides) -> str:
    payload = {
        "user_id": 1,
        "task_id": 2,
        "linked_only": False,
        "mode": "hybrid",
        "top_k": 5,
        "title": "整理周报",
        "description": "总结本周进展",
        "versions": None,
    }
    payload.update(overrides)
    return build_key(**payload)


def test_same_inputs_produce_same_key():
    assert _key() == _key()


def test_task_content_changes_key():
    assert _key() != _key(description="改成总结本月进展")


def test_mode_top_k_and_scope_change_key():
    assert _key() != _key(mode="vector")
    assert _key() != _key(top_k=3)
    assert _key() != _key(linked_only=True)


def test_linked_document_versions_change_key():
    assert _key(versions=[(7, 1, None)]) != _key(versions=[(7, 2, None)])
    assert _key(versions=[(7, 1, None)]) != _key(
        versions=[(7, 1, "2026-11-17T12:00:00")],
    )
