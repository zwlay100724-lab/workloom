"""P1-13 回归：RAG 请求参数必须有边界。"""
import pytest
from app.schemas.rag import RAGQueryRequest
from pydantic import ValidationError


@pytest.mark.parametrize("top_k", [1, 20])
def test_top_k_accepts_bounds(top_k):
    request = RAGQueryRequest(question="年假多少天", top_k=top_k)
    assert request.top_k == top_k


@pytest.mark.parametrize("top_k", [0, -1, 21])
def test_top_k_rejects_out_of_range(top_k):
    with pytest.raises(ValidationError):
        RAGQueryRequest(question="年假多少天", top_k=top_k)


def test_question_must_be_non_empty_and_bounded():
    with pytest.raises(ValidationError):
        RAGQueryRequest(question="")

    with pytest.raises(ValidationError):
        RAGQueryRequest(question="x" * 5001)


def test_ids_must_be_positive():
    with pytest.raises(ValidationError):
        RAGQueryRequest(question="q", document_id=0)

    with pytest.raises(ValidationError):
        RAGQueryRequest(question="q", conversation_id=0)
