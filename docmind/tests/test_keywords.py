"""查询侧关键词抽取的单元测试。"""
from app.rag.keywords import extract_keywords


def test_extracts_terms_from_long_chinese_question():
    keywords = extract_keywords("第一周目标是什么")

    assert "第一周" in keywords
    assert "目标" in keywords


def test_extracts_mixed_english_terms():
    keywords = extract_keywords("Redis 缓存")

    lowered = {keyword.lower() for keyword in keywords}
    assert "redis" in lowered
    assert "缓存" in lowered


def test_filters_stopwords_and_generic_words():
    keywords = extract_keywords("请帮我总结一下目前还剩下哪些必须完成的事情")

    assert "一下" not in keywords
    assert "目前" not in keywords
    assert "哪些" not in keywords
    assert "事情" not in keywords
    assert "完成" in keywords


def test_keeps_domain_phrase_together():
    keywords = extract_keywords("截止日期是什么时候")

    assert "截止日期" in keywords
    assert "时候" not in keywords


def test_empty_query_returns_empty():
    assert extract_keywords("   ") == []


def test_falls_back_to_query_when_all_filtered():
    assert extract_keywords("什么") == ["什么"]


def test_deduplicates_case_insensitively():
    keywords = extract_keywords("Redis redis Redis")

    redis_items = [
        keyword for keyword in keywords if keyword.lower() == "redis"
    ]
    assert len(redis_items) == 1
