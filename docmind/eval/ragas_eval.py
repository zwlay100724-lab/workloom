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
import hashlib
import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

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

DEFAULT_BATCH_SIZE = 8
CONTEXT_METRICS = {
    "faithfulness",
    "context_precision",
    "context_recall",
}
ANSWER_METRICS = {"faithfulness", "answer_relevancy"}


def build_judge(judge_model: str | None = None):
    """构造 Ragas 评委 LLM 和 embedding。

    LLM 使用项目配置的 OpenAI 兼容接口（当前 DeepSeek）；
    embedding 使用 DashScope 的 OpenAI 兼容 endpoint。
    """
    llm_client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
    # Ragas 评委需要输出较长的 JSON。推理模型在长上下文下会把
    # completion 预算全部花在 reasoning_content 上，最终 content 为空；
    # 默认允许用更快的非推理模型，例如 deepseek-flash。
    judge_model = (
        judge_model
        or settings.RAGAS_JUDGE_MODEL
        or settings.llm_model_id
    )
    llm = llm_factory(
        judge_model,
        client=llm_client,
        max_tokens=8192,
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


def compute_rows_hash(rows: list[dict]) -> str:
    """给 RAG rows 生成稳定指纹，避免 checkpoint 和题集错配。"""
    payload = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def default_checkpoint_path(rows: list[dict]) -> Path:
    return (
        Path(__file__).resolve().parent
        / f"ragas_checkpoint_{compute_rows_hash(rows)[:16]}.jsonl"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_checkpoint(
    path: Path,
    *,
    rows_hash: str,
    row_index: int,
    metric: str,
    status: str,
    value: float | None,
    reason: str | None = None,
    error: str | None = None,
) -> None:
    """追加一条评分结果并立即 fsync，进程崩溃也不丢已完成项。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "rows_hash": rows_hash,
        "row_index": row_index,
        "metric": metric,
        "status": status,
        "value": value,
        "updated_at": _now_iso(),
    }
    if reason:
        record["reason"] = reason
    if error:
        record["error"] = str(error)[:1000]

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_checkpoint(
    path: Path,
    rows_hash: str,
) -> dict[tuple[int, str], dict]:
    """读取 JSONL checkpoint；同一 key 以最后一行为准。"""
    if not path.exists():
        return {}

    checkpoint: dict[tuple[int, str], dict] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(
                    f"[warn] 忽略 checkpoint 坏行 "
                    f"{path}:{line_number}"
                )
                continue

            if record.get("rows_hash") != rows_hash:
                raise SystemExit(
                    "checkpoint 与当前 RAG rows 不匹配。"
                    "请使用 --restart-scores，或指定新的 --checkpoint。"
                )

            key = (
                int(record["row_index"]),
                str(record["metric"]),
            )
            checkpoint[key] = record

    return checkpoint


def _unevaluable_reason(
    metric_name: str,
    row: dict,
) -> str | None:
    """返回跳过原因；None 表示这条可以评分。"""
    answer = (row.get("answer") or "").strip()
    contexts = row.get("contexts") or []
    ground_truth = (row.get("ground_truth") or "").strip()

    if metric_name in ANSWER_METRICS and not answer:
        return "empty_answer"
    if metric_name in CONTEXT_METRICS and not contexts:
        return "no_contexts"
    if metric_name in {"context_precision", "context_recall"} and not ground_truth:
        return "missing_ground_truth"
    return None


def _should_score(entry: dict | None, retry_errors: bool) -> bool:
    if entry is None:
        return True
    if entry.get("status") == "error":
        return retry_errors
    return False


def _batch_values(metric, inputs: list[dict]) -> list[float | None]:
    results = metric.batch_score(inputs)
    if len(results) != len(inputs):
        raise ValueError(
            "batch_score 返回数量不一致: "
            f"{len(results)} != {len(inputs)}"
        )
    values: list[float | None] = []
    for result in results:
        value = getattr(result, "value", None)
        values.append(None if value is None else float(value))
    return values


def score_rows_checkpointed(
    rows: list[dict],
    *,
    metrics: dict | None = None,
    judge_model: str | None = None,
    checkpoint_path: Path | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    retry_errors: bool = False,
    restart: bool = False,
) -> tuple[dict[str, list[float | None]], dict[str, dict]]:
    """按指标分批评分，并支持增量落盘 / 断点续跑。

    返回 (scores, stats)：
    - scores[name][row_index] 为分数或 None；
    - stats[name] = {ok, skipped, error, missing, value}。
    """
    if not rows:
        raise ValueError("rows 不能为空")
    if batch_size < 1:
        raise ValueError("batch_size 必须 >= 1")

    if metrics is None:
        llm, embeddings = build_judge(judge_model)
        metrics = build_metrics(llm, embeddings)

    rows_hash = compute_rows_hash(rows)
    path = checkpoint_path or default_checkpoint_path(rows)
    if restart and path.exists():
        path.unlink()
        print(f"已删除旧 checkpoint: {path}")

    checkpoint = load_checkpoint(path, rows_hash)
    print(f"checkpoint: {path}")
    print(f"已有评分结果: {len(checkpoint)} 项")

    # 先把不适用评分的项记为 skipped，避免每次都重复判断。
    for name in metrics:
        for index, row in enumerate(rows):
            key = (index, name)
            if key in checkpoint:
                continue
            reason = _unevaluable_reason(name, row)
            if reason is None:
                continue
            _append_checkpoint(
                path,
                rows_hash=rows_hash,
                row_index=index,
                metric=name,
                status="skipped",
                value=None,
                reason=reason,
            )
            checkpoint[key] = {
                "status": "skipped",
                "value": None,
                "reason": reason,
            }

    for name, (metric, build_input) in metrics.items():
        pending = [
            index
            for index in range(len(rows))
            if _should_score(
                checkpoint.get((index, name)),
                retry_errors,
            )
        ]
        total = len(pending)
        print(f"[{name}] pending={total}/{len(rows)}")
        started = perf_counter()
        processed = 0

        for start in range(0, total, batch_size):
            batch = pending[start:start + batch_size]
            inputs = [build_input(rows[index]) for index in batch]

            try:
                values = _batch_values(metric, inputs)
                for index, value in zip(batch, values):
                    _append_checkpoint(
                        path,
                        rows_hash=rows_hash,
                        row_index=index,
                        metric=name,
                        status="ok",
                        value=value,
                    )
                    checkpoint[(index, name)] = {
                        "status": "ok",
                        "value": value,
                    }
                batch_ok = len(batch)
                batch_error = 0
            except Exception as exc:  # noqa: BLE001
                # 整批失败时退化为逐条，避免一条坏数据拖垮整批。
                print(
                    f"  [warn] {name} 批次失败，退化为逐条: "
                    f"{type(exc).__name__}: {exc}"
                )
                batch_ok = 0
                batch_error = 0
                for index, input_item in zip(batch, inputs):
                    try:
                        value = _batch_values(
                            metric,
                            [input_item],
                        )[0]
                        _append_checkpoint(
                            path,
                            rows_hash=rows_hash,
                            row_index=index,
                            metric=name,
                            status="ok",
                            value=value,
                        )
                        checkpoint[(index, name)] = {
                            "status": "ok",
                            "value": value,
                        }
                        batch_ok += 1
                    except Exception as row_exc:  # noqa: BLE001
                        _append_checkpoint(
                            path,
                            rows_hash=rows_hash,
                            row_index=index,
                            metric=name,
                            status="error",
                            value=None,
                            error=(
                                f"{type(row_exc).__name__}: "
                                f"{row_exc}"
                            ),
                        )
                        checkpoint[(index, name)] = {
                            "status": "error",
                            "value": None,
                            "error": str(row_exc),
                        }
                        batch_error += 1
                        print(
                            f"  [warn] {name} 第 {index + 1} 条失败: "
                            f"{type(row_exc).__name__}: {row_exc}"
                        )

            processed += len(batch)
            print(
                f"[{name}] {processed}/{total} "
                f"batch_ok={batch_ok} batch_error={batch_error} "
                f"elapsed={perf_counter() - started:.1f}s"
            )

    scores: dict[str, list[float | None]] = {}
    stats: dict[str, dict] = {}
    for name in metrics:
        values: list[float | None] = []
        counts = {
            "ok": 0,
            "skipped": 0,
            "error": 0,
            "missing": 0,
            "value": 0,
        }
        for index in range(len(rows)):
            entry = checkpoint.get((index, name))
            if entry is None:
                counts["missing"] += 1
                values.append(None)
                continue

            status = str(entry.get("status") or "missing")
            counts[status] = counts.get(status, 0) + 1
            value = entry.get("value")
            if status == "ok" and value is not None:
                counts["value"] += 1
                values.append(float(value))
            else:
                values.append(None)

        scores[name] = values
        stats[name] = counts
        if counts["missing"]:
            print(
                f"[warn] {name} 还有 {counts['missing']} 项未完成，"
                "下次运行会继续补跑"
            )

    return scores, stats


def average(values: list[float | None]) -> float | None:
    real = [value for value in values if value is not None]
    if not real:
        return None
    return round(sum(real) / len(real), 4)
