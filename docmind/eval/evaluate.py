#!/usr/bin/env python3
"""DocMind 端到端 Ragas 评测。

流程：
1. 读取 questions.json；
2. 对每个问题调用 RAGService.query（真实 LangGraph + 检索 + LLM）；
3. 使用同一次回答实际使用的 contexts 构造评测数据；
4. 用 Ragas 0.4 的现代 metrics 计算四项指标；
5. 输出 eval/report.json。

用法（在 docmind/ 下）：
    ../.venv/bin/python eval/index_sample.py   # 首次运行：先索引示例语料
    ../.venv/bin/python eval/evaluate.py
    ../.venv/bin/python eval/evaluate.py --limit 1 --skip-ragas

Ragas 的版本兼容与评分细节集中在 eval/ragas_eval.py。
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

DOCMIND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "questions.json"
DEFAULT_REPORT = Path(__file__).resolve().parent / "report.json"

load_dotenv(DOCMIND_ROOT / ".env")
sys.path.insert(0, str(DOCMIND_ROOT))

from app.core.config import settings  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.services.rag_service import RAGService  # noqa: E402
from ragas_eval import METRIC_NAMES, average, score_rows  # noqa: E402
from shared.db.database import engine  # noqa: E402
from shared.logging import configure_logging  # noqa: E402
from sqlmodel import Session, func, select  # noqa: E402

configure_logging()


def load_questions(path: Path, limit: int | None) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"测试集不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"{path} 必须是非空 JSON 数组")
    for index, item in enumerate(data, 1):
        if (
            not isinstance(item, dict)
            or not item.get("question")
            or not item.get("ground_truth")
        ):
            raise SystemExit(f"第 {index} 条缺少 question / ground_truth")
    return data[:limit] if limit else data


def has_completed_documents(session: Session, user_id: int) -> bool:
    count = session.exec(
        select(func.count())
        .select_from(Document)
        .where(
            Document.user_id == user_id,
            Document.status == "completed",
        )
    ).one()
    return count > 0


def collect_rows(
    session: Session,
    questions: list[dict],
    user_id: int,
    top_k: int,
) -> list[dict]:
    rag = RAGService(session)
    rows: list[dict] = []

    for index, item in enumerate(questions, 1):
        started = time.perf_counter()
        try:
            result = rag.query(
                question=item["question"],
                user_id=user_id,
                top_k=top_k,
            )
        except Exception as exc:  # noqa: BLE001 — 单题失败不应中断整轮评测
            latency_ms = int((time.perf_counter() - started) * 1000)
            print(
                f"[{index}/{len(questions)}] 失败: "
                f"{type(exc).__name__}: {exc}"
            )
            rows.append(
                {
                    "question": item["question"],
                    "ground_truth": item["ground_truth"],
                    "answer": "",
                    "contexts": [],
                    "sources": [],
                    "expected_source": item.get("expected_source"),
                    "retrieval_hit": None,
                    "latency_ms": latency_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        latency_ms = int((time.perf_counter() - started) * 1000)
        contexts = result.get("contexts") or []
        sources = result.get("sources") or []
        expected_source = item.get("expected_source")
        retrieval_hit = None
        if expected_source:
            retrieval_hit = any(
                expected_source in (source.get("document") or "")
                for source in sources
            )

        rows.append(
            {
                "question": item["question"],
                "ground_truth": item["ground_truth"],
                "answer": result["answer"],
                "contexts": contexts,
                "sources": sources,
                "expected_source": expected_source,
                "retrieval_hit": retrieval_hit,
                "latency_ms": latency_ms,
                "error": None,
            }
        )
        print(
            f"[{index}/{len(questions)}] {item['question']} | "
            f"{latency_ms} ms | contexts={len(contexts)}"
        )
        if not contexts:
            print("  [warn] 没有检索到上下文，context 指标会跳过")

    return rows


def summarize(rows: list[dict], scores: dict) -> dict:
    hits = [
        row["retrieval_hit"]
        for row in rows
        if row["retrieval_hit"] is not None
    ]
    return {
        "questions": len(rows),
        "errors": sum(1 for row in rows if row.get("error")),
        "retrieval_hit_rate": (
            round(sum(hits) / len(hits), 4) if hits else None
        ),
        "avg_latency_ms": round(
            sum(row["latency_ms"] for row in rows) / len(rows), 1
        ),
        "ragas": {
            name: average(values) for name, values in scores.items()
        },
    }


def write_report(
    path: Path,
    rows: list[dict],
    scores: dict,
    summary: dict,
    top_k: int,
) -> None:
    details = []
    for index, row in enumerate(rows):
        details.append(
            {
                **row,
                "metrics": {
                    name: values[index]
                    for name, values in scores.items()
                },
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llm_model": settings.llm_model_id,
        "embedding_model": settings.EMBED_MODEL_ID,
        "top_k": top_k,
        "summary": summary,
        "details": details,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_summary(summary: dict) -> None:
    print("\n" + "=" * 60)
    print("评测汇总")
    print("=" * 60)
    print(f"Questions:           {summary['questions']}")
    print(f"Errors:              {summary['errors']}")
    print(f"Retrieval Hit Rate:  {summary['retrieval_hit_rate']}")
    print(f"Avg Latency:         {summary['avg_latency_ms']} ms")
    for name, value in summary["ragas"].items():
        print(f"{name:20s} {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DocMind Ragas 评测")
    parser.add_argument(
        "--questions",
        type=Path,
        default=DEFAULT_QUESTIONS,
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=settings.TOP_K)
    parser.add_argument(
        "--skip-ragas",
        action="store_true",
        help="只跑 RAG 与检索命中率，不调用 Ragas 评委模型",
    )
    args = parser.parse_args()

    questions = load_questions(args.questions, args.limit)

    with Session(engine) as session:
        if not has_completed_documents(session, args.user_id):
            raise SystemExit(
                "没有已完成的文档。先运行: "
                "../.venv/bin/python eval/index_sample.py"
            )
        rows = collect_rows(
            session,
            questions,
            args.user_id,
            args.top_k,
        )

    if args.skip_ragas:
        scores = {
            name: [None] * len(rows) for name in METRIC_NAMES
        }
        print("\n[skip] --skip-ragas，未调用 Ragas 评委模型")
    else:
        scores = score_rows(rows)

    summary = summarize(rows, scores)
    write_report(args.report, rows, scores, summary, args.top_k)
    print_summary(summary)
    print(f"\n报告已写入: {args.report}")


if __name__ == "__main__":
    main()
