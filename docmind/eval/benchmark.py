#!/usr/bin/env python3
"""DocMind 检索模式 / chunk size 对比基准。

实验矩阵：
  chunk_size ∈ {300, 500, 800}（overlap = size / 5）
  mode       ∈ {vector, hybrid, rewrite}

每个 cell：
  1. 用指定 chunk size 重建独立评测库 docmind_eval 的索引；
  2. 对每条问题执行检索，统计 expected_key 命中率和检索延迟；
  3. 可选（--with-ragas）用固定 Prompt 生成答案，再算 Ragas 四项指标。

使用（在 docmind/ 下）：
    ../.venv/bin/python eval/benchmark.py
    ../.venv/bin/python eval/benchmark.py --with-ragas --limit 3
    ../.venv/bin/python eval/benchmark.py --sizes 300,500,800 \
        --modes vector,hybrid,rewrite

说明：
- 会真实调用 Embedding / LLM；--with-ragas 的调用量更大；
- 评测库默认是本地 PostgreSQL 的 docmind_eval，不碰 docmind 开发库；
- 也支持通过 DOCMIND_EVAL_DATABASE_URL 指定独立评测库（库名必须以
  _eval 或 _test 结尾，避免误删业务库）。
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlmodel import Session, SQLModel

DOCMIND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = Path(__file__).resolve().parent / "benchmark_corpus.txt"
DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "benchmark_questions.json"
DEFAULT_REPORT = Path(__file__).resolve().parent / "benchmark_report.json"
ALLOWED_MODES = {"vector", "hybrid", "rewrite"}

load_dotenv(DOCMIND_ROOT / ".env")
sys.path.insert(0, str(DOCMIND_ROOT))

from app.core.config import settings  # noqa: E402
from app.llm.client import get_chat_model  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.document_chunk import DocumentChunk  # noqa: E402
from app.rag.embeddings import get_embeddings  # noqa: E402
from app.rag.formatting import format_context  # noqa: E402
from app.rag.loaders import load_document  # noqa: E402
from app.rag.splitters import split_documents  # noqa: E402
from app.repositories.vector_repository import VectorRepository  # noqa: E402
from app.services.retrieval_service import RetrievalService  # noqa: E402
from ragas_eval import (  # noqa: E402
    average,
    build_judge,
    build_metrics,
    score_rows,
)
from shared.logging import configure_logging  # noqa: E402

configure_logging()

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是企业知识库助手。只根据给定上下文回答问题；"
            "上下文没有的信息不要编造。回答尽量简洁。",
        ),
        (
            "human",
            "上下文：\n{context}\n\n问题：{question}",
        ),
    ]
)
ANSWER_CHAIN = ANSWER_PROMPT | get_chat_model() | StrOutputParser()


def _normalize(text: str) -> str:
    return "".join(text.lower().split())


def find_hit_rank(contexts: list[str], key: str) -> int | None:
    """返回第一个包含 expected_key 的片段下标；没有则 None。"""
    target = _normalize(key)
    for index, context in enumerate(contexts):
        if target in _normalize(context):
            return index
    return None


def load_questions(path: Path, limit: int | None) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"问题集不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"{path} 必须是非空 JSON 数组")
    for index, item in enumerate(data, 1):
        for key in ("question", "ground_truth", "expected_key"):
            if not isinstance(item, dict) or not item.get(key):
                raise SystemExit(f"第 {index} 条缺少 {key}")
        if "history" in item and not isinstance(item["history"], list):
            raise SystemExit(f"第 {index} 条 history 必须是数组")
    return data[:limit] if limit else data


def get_eval_engine():
    override = os.getenv("DOCMIND_EVAL_DATABASE_URL")
    if override:
        base = make_url(override)
        eval_db = base.database
        if not eval_db or not (
            eval_db.endswith("_eval") or eval_db.endswith("_test")
        ):
            raise SystemExit(
                "DOCMIND_EVAL_DATABASE_URL 的库名必须以 "
                "_eval 或 _test 结尾"
            )
    else:
        env_path = DOCMIND_ROOT / ".env"
        if not env_path.exists():
            raise SystemExit("缺少 docmind/.env，无法确定数据库连接")
        base_url = dotenv_values(env_path).get("DATABASE_URL")
        if not base_url or not base_url.startswith("postgresql"):
            raise SystemExit("基准测试需要 PostgreSQL")
        base = make_url(base_url)
        eval_db = "docmind_eval"

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", eval_db):
        raise SystemExit(f"非法数据库名: {eval_db}")

    admin = create_engine(
        base.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": eval_db},
        ).scalar_one_or_none()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{eval_db}"'))
            print(f"[info] 已创建评测库 {eval_db}")
    admin.dispose()

    engine = create_engine(base.set(database=eval_db))
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    # 评测库每次重建表结构；只操作 document / chunk 两张表。
    SQLModel.metadata.drop_all(
        engine,
        tables=[DocumentChunk.__table__, Document.__table__],
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[Document.__table__, DocumentChunk.__table__],
    )
    return engine


def rebuild_index(
    engine,
    corpus_path: Path,
    chunk_size: int,
    chunk_overlap: int,
    user_id: int,
) -> int:
    with Session(engine) as session:
        session.execute(
            text(
                "TRUNCATE document_chunks, documents "
                "RESTART IDENTITY CASCADE"
            )
        )
        session.commit()

        documents = load_document(str(corpus_path))
        chunks = split_documents(
            documents,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        if not chunks:
            raise SystemExit("语料切分后没有 chunk，请检查 corpus")

        vectors = get_embeddings().embed_documents(
            [chunk.page_content for chunk in chunks]
        )

        now = datetime.utcnow()
        document = Document(
            filename=corpus_path.name,
            file_path=str(corpus_path),
            file_type="text",
            user_id=user_id,
            status="completed",
            page_count=len(documents),
            chunk_count=len(chunks),
            created_at=now,
            updated_at=now,
        )
        session.add(document)
        session.commit()
        session.refresh(document)

        VectorRepository(session).add_chunks(
            chunks=chunks,
            vectors=vectors,
            document_id=document.id,
            user_id=user_id,
        )
        # add_chunks 不再自行 commit（生产索引要和其他写操作同事务），
        # 基准脚本在这里显式提交。
        session.commit()
        return len(chunks)


def retrieve_docs(
    service: RetrievalService,
    session: Session,
    mode: str,
    item: dict,
    user_id: int,
    top_k: int,
):
    history = item.get("history") or []
    if mode == "vector":
        return service.retrieve(
            session,
            item["question"],
            user_id=user_id,
            top_k=top_k,
        )
    if mode == "hybrid":
        return service.retrieve_hybrid(
            session,
            item["question"],
            user_id=user_id,
            top_k=top_k,
        )
    if mode == "rewrite":
        return service.retrieve_with_rewrite(
            session,
            item["question"],
            user_id=user_id,
            top_k=top_k,
            history=history,
            enable_rewrite=True,
        )
    raise ValueError(f"未知 mode: {mode}")


def generate_answer(question: str, docs: list) -> str:
    if not docs:
        return "知识库中没有找到相关内容。"
    answer = ANSWER_CHAIN.invoke(
        {
            "context": format_context(docs),
            "question": question,
        }
    )
    return answer.strip() or "知识库中没有找到相关内容。"


def run_cell(
    service: RetrievalService,
    session: Session,
    mode: str,
    item: dict,
    user_id: int,
    top_k: int,
    with_ragas: bool,
) -> dict:
    started = time.perf_counter()
    error = None
    try:
        docs = retrieve_docs(
            service,
            session,
            mode,
            item,
            user_id,
            top_k,
        )
    except Exception as exc:  # noqa: BLE001 — 单个 cell 失败不应中断矩阵
        docs = []
        error = f"{type(exc).__name__}: {exc}"
    retrieval_ms = int((time.perf_counter() - started) * 1000)

    contexts = [doc.page_content for doc in docs]
    answer = ""
    answer_ms = 0
    if with_ragas:
        started = time.perf_counter()
        try:
            answer = generate_answer(item["question"], docs)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        answer_ms = int((time.perf_counter() - started) * 1000)

    hit_rank = find_hit_rank(contexts, item["expected_key"])
    hit = hit_rank is not None

    return {
        "question": item["question"],
        "ground_truth": item["ground_truth"],
        "answer": answer,
        "contexts": contexts,
        "hit": hit,
        "hit_rank": hit_rank,
        "hit_at_1": hit_rank == 0,
        "reciprocal_rank": (
            1.0 / (hit_rank + 1) if hit_rank is not None else 0.0
        ),
        "retrieval_ms": retrieval_ms,
        "answer_ms": answer_ms,
        "context_count": len(contexts),
        "error": error,
    }


def print_table(results: list[dict], with_ragas: bool) -> None:
    header = (
        f"{'size':>5} {'mode':<8} {'chunks':>6} "
        f"{'hit':>6} {'hit1':>6} {'mrr':>6} "
        f"{'ret_ms':>9} {'ans_ms':>9}"
    )
    if with_ragas:
        header += "   faith   relev   cprec    crec"
    print("\n" + header)
    print("-" * len(header))
    for result in results:
        line = (
            f"{result['chunk_size']:>5} "
            f"{result['mode']:<8} "
            f"{result['chunk_count']:>6} "
            f"{result['hit_rate']:>6.2f} "
            f"{result['hit_at_1_rate']:>6.2f} "
            f"{result['mrr']:>6.2f} "
            f"{result['avg_retrieval_ms']:>9.1f} "
            f"{result['avg_answer_ms']:>9.1f}"
        )
        if with_ragas:
            ragas = result.get("ragas") or {}
            line += "".join(
                f"   {ragas.get(name) if ragas.get(name) is not None else '-':>5}"
                for name in (
                    "faithfulness",
                    "answer_relevancy",
                    "context_precision",
                    "context_recall",
                )
            )
        print(line)


def main() -> None:
    parser = argparse.ArgumentParser(description="DocMind 检索/切块基准")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--questions",
        type=Path,
        default=DEFAULT_QUESTIONS,
    )
    parser.add_argument("--sizes", default="300,500,800")
    parser.add_argument("--modes", default="vector,hybrid,rewrite")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--question-indices",
        default=None,
        help="只跑指定下标的题目，例如 0,3,6,7",
    )
    parser.add_argument("--top-k", type=int, default=settings.TOP_K)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument(
        "--with-ragas",
        action="store_true",
        help="生成答案并计算 Ragas 指标（调用量较大）",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    if not args.corpus.exists():
        raise SystemExit(f"语料不存在: {args.corpus}")
    questions = load_questions(args.questions, None)
    if args.question_indices:
        try:
            indices = [
                int(value)
                for value in args.question_indices.split(",")
                if value.strip()
            ]
        except ValueError as exc:
            raise SystemExit("--question-indices 必须是逗号分隔的整数") from exc
        if not indices or any(
            index < 0 or index >= len(questions) for index in indices
        ):
            raise SystemExit("--question-indices 下标越界")
        questions = [questions[index] for index in indices]
    if args.limit:
        questions = questions[: args.limit]

    sizes = [
        int(value)
        for value in args.sizes.split(",")
        if value.strip()
    ]
    modes = [
        value.strip()
        for value in args.modes.split(",")
        if value.strip()
    ]
    if not sizes or any(size <= 0 for size in sizes):
        raise SystemExit("--sizes 必须是正整数")
    if not modes or any(mode not in ALLOWED_MODES for mode in modes):
        raise SystemExit(
            f"--modes 只能是 {sorted(ALLOWED_MODES)}"
        )

    # 复制到 data/，避免将来删除文档时误删仓库里的评测语料。
    stored_corpus = DOCMIND_ROOT / "data" / args.corpus.name
    stored_corpus.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.corpus, stored_corpus)

    engine = get_eval_engine()

    metrics = None
    if args.with_ragas:
        llm, embeddings = build_judge()
        metrics = build_metrics(llm, embeddings)
        print(
            "[info] --with-ragas 已开启：每个 cell 都会生成答案并调用评委"
        )

    results: list[dict] = []
    for size in sizes:
        overlap = max(1, size // 5)
        chunk_count = rebuild_index(
            engine,
            stored_corpus,
            size,
            overlap,
            args.user_id,
        )
        print(f"\n[info] chunk_size={size}, overlap={overlap}, "
              f"chunks={chunk_count}")

        service = RetrievalService()
        with Session(engine) as session:
            for mode in modes:
                rows = [
                    run_cell(
                        service,
                        session,
                        mode,
                        item,
                        args.user_id,
                        args.top_k,
                        args.with_ragas,
                    )
                    for item in questions
                ]
                ragas_scores = (
                    score_rows(rows, metrics) if metrics else None
                )
                ragas_summary = (
                    {
                        name: average(values)
                        for name, values in ragas_scores.items()
                    }
                    if ragas_scores
                    else None
                )

                details = []
                for index, row in enumerate(rows):
                    detail = {
                        "question": row["question"],
                        "hit": row["hit"],
                        "hit_rank": row["hit_rank"],
                        "hit_at_1": row["hit_at_1"],
                        "reciprocal_rank": row["reciprocal_rank"],
                        "retrieval_ms": row["retrieval_ms"],
                        "answer_ms": row["answer_ms"],
                        "context_count": row["context_count"],
                        "error": row["error"],
                    }
                    if ragas_scores:
                        detail["ragas"] = {
                            name: values[index]
                            for name, values in ragas_scores.items()
                        }
                    details.append(detail)

                results.append(
                    {
                        "chunk_size": size,
                        "chunk_overlap": overlap,
                        "chunk_count": chunk_count,
                        "mode": mode,
                        "hit_rate": round(
                            sum(row["hit"] for row in rows) / len(rows),
                            4,
                        ),
                        "hit_at_1_rate": round(
                            sum(row["hit_at_1"] for row in rows)
                            / len(rows),
                            4,
                        ),
                        "mrr": round(
                            sum(row["reciprocal_rank"] for row in rows)
                            / len(rows),
                            4,
                        ),
                        "avg_retrieval_ms": round(
                            sum(row["retrieval_ms"] for row in rows)
                            / len(rows),
                            1,
                        ),
                        "avg_answer_ms": round(
                            sum(row["answer_ms"] for row in rows)
                            / len(rows),
                            1,
                        ),
                        "ragas": ragas_summary,
                        "details": details,
                    }
                )
                print(
                    f"[ok] size={size} mode={mode} "
                    f"hit={results[-1]['hit_rate']} "
                    f"hit@1={results[-1]['hit_at_1_rate']} "
                    f"mrr={results[-1]['mrr']}"
                )

    print_table(results, args.with_ragas)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": str(args.corpus),
        "questions": len(questions),
        "question_indices": args.question_indices,
        "top_k": args.top_k,
        "with_ragas": args.with_ragas,
        "sizes": sizes,
        "modes": modes,
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n报告已写入: {args.report}")


if __name__ == "__main__":
    main()
