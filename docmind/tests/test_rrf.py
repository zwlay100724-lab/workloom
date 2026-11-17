"""加权 RRF 融合的单元测试。"""
import pytest
from app.repositories.vector_repository import (
    LEXICAL_ROUTE_WEIGHT,
    RRF_K,
    VECTOR_ROUTE_WEIGHT,
    _combine_lexical_ranks,
    _rrf_scores,
)


def test_combine_lexical_ranks_keeps_best_rank():
    merged = _combine_lexical_ranks([
        {1: 1, 2: 5},
        {1: 3, 3: 2},
    ])

    assert merged == {1: 1, 2: 5, 3: 2}


def test_rrf_counts_merged_lexical_once():
    scores = _rrf_scores({1: 1}, {1: 1})

    expected = (
        VECTOR_ROUTE_WEIGHT / (RRF_K + 1)
        + LEXICAL_ROUTE_WEIGHT / (RRF_K + 1)
    )
    assert scores[1] == pytest.approx(expected)


def test_rrf_lexical_weight_changes_ranking():
    equal = _rrf_scores(
        {1: 1, 2: 2},
        {2: 1},
        lexical_weight=1.0,
    )
    vector_only = _rrf_scores(
        {1: 1, 2: 2},
        {2: 1},
        lexical_weight=0.0,
    )

    # 词法权重高时，只靠关键词命中的 doc2 能反超文档1。
    assert equal[2] > equal[1]
    # 词法权重归零时，向量排名重新占优。
    assert vector_only[1] > vector_only[2]
