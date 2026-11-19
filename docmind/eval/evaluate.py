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

只补跑评分（复用已保存的 RAG 结果）：
    ../.venv/bin/python eval/evaluate.py --rows eval/rag_rows.json
    ../.venv/bin/python eval/evaluate.py --rows eval/rag_rows.json --retry-errors

评分按指标分批，每批立即写入 checkpoint；中断后重跑同一条命令即可续跑。

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
DEFAULT_ROWS = Path(__file__).resolve().parent / "rag_rows.json"

load_dotenv(DOCMIND_ROOT / ".env")
sys.path.insert(0, str(DOCMIND_ROOT))

from app.core.config import settings  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.services.rag_service import RAGService  # noqa: E402
from ragas_eval import (  # noqa: E402
    METRIC_NAMES,
    average,
    default_checkpoint_path,
    score_rows_checkpointed,
)
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


def load_rows(path: Path) -> list[dict]:
    """加载之前保存的 RAG 结果，跳过检索与生成阶段。"""
    if not path.exists():
        raise SystemExit(f"RAG rows 不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"{path} 必须是非空 JSON 数组")
    for index, item in enumerate(data, 1):
        if (
            not isinstance(item, dict)
            or "question" not in item
            or "contexts" not in item
        ):
            raise SystemExit(f"第 {index} 条缺少 question / contexts")
    return data


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
        # Ragas 用最终裁剪后的证据上下文；全量并集只作为调试信息。
        all_contexts = result.get("contexts") or []
        contexts = result.get("final_contexts") or all_contexts
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
                "all_context_count": len(all_contexts),
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
    *,
    checkpoint_path: Path | None = None,
    metric_stats: dict | None = None,
    judge_model: str | None = None,
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
        "judge_llm_model": (
            judge_model
            or settings.RAGAS_JUDGE_MODEL
            or settings.llm_model_id
        ),
        "embedding_model": settings.EMBED_MODEL_ID,
        "top_k": top_k,
        "checkpoint": (
            str(checkpoint_path) if checkpoint_path is not None else None
        ),
        "metric_stats": metric_stats or {},
        "summary": summary,
        "details": details,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_summary(
    summary: dict,
    metric_stats: dict | None = None,
) -> None:
    print("\n" + "=" * 60)
    print("评测汇总")
    print("=" * 60)
    print(f"Questions:           {summary['questions']}")
    print(f"Errors:              {summary['errors']}")
    print(f"Retrieval Hit Rate:  {summary['retrieval_hit_rate']}")
    print(f"Avg Latency:         {summary['avg_latency_ms']} ms")
    for name, value in summary["ragas"].items():
        print(f"{name:20s} {value}")

    if metric_stats:
        print("\n指标状态")
        for name, counts in metric_stats.items():
            print(
                f"  {name:20s} "
                f"ok={counts.get('ok', 0)} "
                f"skipped={counts.get('skipped', 0)} "
                f"error={counts.get('error', 0)} "
                f"missing={counts.get('missing', 0)}"
            )


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
        "--rows",
        type=Path,
        default=None,
        help="直接使用已保存的 RAG 结果，跳过检索与生成（只补跑 Ragas）",
    )
    parser.add_argument(
        "--from-report",
        type=Path,
        default=None,
        help="从已有 report.json 的 details 中恢复 RAG 结果，只补跑 Ragas",
    )
    parser.add_argument(
        "--rows-out",
        type=Path,
        default=DEFAULT_ROWS,
        help="RAG 收集完成后保存的中间结果路径",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="Ragas 评分 checkpoint；默认按 RAG rows 指纹生成",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="同一指标每批评分的条数",
    )
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="重试 checkpoint 中 status=error 的项",
    )
    parser.add_argument(
        "--restart-scores",
        action="store_true",
        help="删除 checkpoint，从头开始 Ragas 评分",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="覆盖 Ragas 评委模型，例如 deepseek-flash",
    )
    parser.add_argument(
        "--skip-ragas",
        action="store_true",
        help="只跑 RAG 与检索命中率，不调用 Ragas 评委模型",
    )
    args = parser.parse_args()

    if args.from_report is not None:
        if not args.from_report.exists():
            raise SystemExit(f"report 不存在: {args.from_report}")
        report = json.loads(
            args.from_report.read_text(encoding="utf-8")
        )
        details = report.get("details") or []
        if not details:
            raise SystemExit(f"{args.from_report} 没有 details")
        rows = [
            {
                key: value
                for key, value in item.items()
                if key != "metrics"
            }
            for item in details
        ]
        print(
            f"从 report 恢复 RAG rows: "
            f"{args.from_report}（{len(rows)} 条）"
        )
    elif args.rows is not None:
        rows = load_rows(args.rows)
        print(f"使用已有 RAG rows: {args.rows}（{len(rows)} 条）")
    else:
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

        # 先落盘：Ragas 评委失败或余额不足时，下次可以只补跑评分，
        # 不必重新消耗 RAG 的 LLM / Embedding 配额。
        args.rows_out.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"RAG rows 已保存: {args.rows_out}")

    checkpoint_path: Path | None = None
    metric_stats: dict = {}

    if args.skip_ragas:
        scores = {
            name: [None] * len(rows) for name in METRIC_NAMES
        }
        print("\n[skip] --skip-ragas，未调用 Ragas 评委模型")
    else:
        checkpoint_path = (
            args.checkpoint or default_checkpoint_path(rows)
        )
        if args.restart_scores and checkpoint_path.exists():
            checkpoint_path.unlink()
            print(f"已删除旧 checkpoint: {checkpoint_path}")

        scores, metric_stats = score_rows_checkpointed(
            rows,
            judge_model=args.judge_model,
            checkpoint_path=checkpoint_path,
            batch_size=args.batch_size,
            retry_errors=args.retry_errors,
        )

    summary = summarize(rows, scores)
    write_report(
        args.report,
        rows,
        scores,
        summary,
        args.top_k,
        checkpoint_path=checkpoint_path,
        metric_stats=metric_stats,
        judge_model=args.judge_model,
    )
    print_summary(summary, metric_stats)
    print(f"\n报告已写入: {args.report}")


if __name__ == "__main__":
    main()
