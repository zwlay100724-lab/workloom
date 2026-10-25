"""Ragas 0.4 评分工具，供 evaluate.py / benchmark.py 复用。

为什么要单独一层：
- evaluate.py（端到端 RAG 评测）和 benchmark.py（检索模式 / chunk size
  对比）需要完全相同的评委、指标和容错逻辑；
- Ragas 0.4.3 有几处版本兼容问题，集中在这里处理，业务脚本只关心数据。

Ragas 0.4.3 的两个兼容点：
1. 它仍 import `langchain_community.chat_models.vertexai`，而
   langchain-community 0.4 已移除该模块；这里注入占位模块（评测不用 VertexAI）。
2. `ragas.evaluate()` 只接受旧版 Metric，不接受 collections 的现代 metric；
   因此逐条调用 `metric.batch_score()`，由调用方汇总。
"""
import sys
import types
from pathlib import Path

from dotenv import load_dotenv

DOCMIND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(DOCMIND_ROOT / ".env")
sys.path.insert(0, str(DOCMIND_ROOT))

try:
    from langchain_community.chat_models import vertexai  # noqa: F401
except ImportError:
    _stub = types.ModuleType(
        "langchain_community.chat_models.vertexai"
    )
    _stub.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules[
        "langchain_community.chat_models.vertexai"
    ] = _stub

from app.core.config import settings  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from ragas.embeddings.base import embedding_factory  # noqa: E402
from ragas.llms import llm_factory  # noqa: E402
from ragas.metrics.collections import (  # noqa: E402
    AnswerRelevancy,
    ContextPrecisionWithReference,
    ContextRecall,
    Faithfulness,
)

METRIC_NAMES = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)


def build_judge():
    """构造 Ragas 评委 LLM 和 embedding。

    LLM 使用项目配置的 OpenAI 兼容接口（当前 DeepSeek）；
    embedding 使用 DashScope 的 OpenAI 兼容 endpoint。
    """
    llm_client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
    # Ragas 评委需要输出较长的推理/JSON；默认 1024 token 在长上下文上
    # 会触发 IncompleteOutputException。
    llm = llm_factory(
        settings.llm_model_id,
        client=llm_client,
        max_tokens=4096,
        temperature=0,
    )

    embed_base_url = settings.EMBED_BASE_URL
    if not embed_base_url and settings.EMBED_MODEL_TYPE == "dashscope":
        embed_base_url = (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    if not settings.EMBED_API_KEY or not embed_base_url:
        raise SystemExit(
            "Ragas 需要 EMBED_API_KEY 和 OpenAI 兼容的 EMBED_BASE_URL"
        )

    embed_client = AsyncOpenAI(
        api_key=settings.EMBED_API_KEY,
        base_url=embed_base_url,
    )
    embeddings = embedding_factory(
        "openai",
        model=settings.EMBED_MODEL_ID,
        client=embed_client,
        interface="modern",
    )
    return llm, embeddings


def build_metrics(llm, embeddings) -> dict:
    """返回 name -> (metric, row -> metric_input) 的映射。"""
    return {
        "faithfulness": (
            Faithfulness(llm=llm),
            lambda row: {
                "user_input": row["question"],
                "response": row["answer"],
                "retrieved_contexts": row["contexts"],
            },
        ),
        "answer_relevancy": (
            AnswerRelevancy(llm=llm, embeddings=embeddings),
            lambda row: {
                "user_input": row["question"],
                "response": row["answer"],
            },
        ),
        "context_precision": (
            ContextPrecisionWithReference(llm=llm),
            lambda row: {
                "user_input": row["question"],
                "reference": row["ground_truth"],
                "retrieved_contexts": row["contexts"],
            },
        ),
        "context_recall": (
            ContextRecall(llm=llm),
            lambda row: {
                "user_input": row["question"],
                "retrieved_contexts": row["contexts"],
                "reference": row["ground_truth"],
            },
        ),
    }


def score_rows(
    rows: list[dict],
    metrics: dict | None = None,
) -> dict[str, list[float | None]]:
    """逐条计算 Ragas 指标；单条失败记为 None，不中断整轮。

    rows 必须包含：
      question / answer / contexts / ground_truth
    """
    if metrics is None:
        llm, embeddings = build_judge()
        metrics = build_metrics(llm, embeddings)

    scores: dict[str, list[float | None]] = {
        name: [] for name in metrics
    }

    for index, row in enumerate(rows, 1):
        for name, (metric, build_input) in metrics.items():
            # 没有上下文时，依赖上下文的指标没有意义。
            if (
                name
                in {"faithfulness", "context_precision", "context_recall"}
                and not row["contexts"]
            ):
                scores[name].append(None)
                continue
            try:
                result = metric.batch_score([build_input(row)])[0]
                value = result.value
                scores[name].append(
                    None if value is None else float(value)
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  [warn] {name} 第 {index} 条失败: "
                    f"{type(exc).__name__}: {exc}"
                )
                scores[name].append(None)

    return scores


def average(values: list[float | None]) -> float | None:
    real = [value for value in values if value is not None]
    if not real:
        return None
    return round(sum(real) / len(real), 4)
