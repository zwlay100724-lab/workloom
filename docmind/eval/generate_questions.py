#!/usr/bin/env python3
"""从已索引文档生成 Ragas 评测题集。

用法（在 docmind/ 下）：
    ../.venv/bin/python eval/generate_questions.py --user-id 28

流程：
1. 取该用户所有 completed 文档的 chunk；
2. 每份文档均匀抽取 N 个 chunk（默认 25）；
3. 分批调用 LLM，为每个 chunk 生成一个问题和简短 ground_truth；
4. 过滤泛泛问题、空答案和与原文重合过低的答案；
5. 写入 eval/questions_100plus.json，供 evaluate.py 使用。

注意：会真实调用 LLM，产生费用。默认目标是 100+ 道题。
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

DOCMIND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "questions_100plus.json"

load_dotenv(DOCMIND_ROOT / ".env")
sys.path.insert(0, str(DOCMIND_ROOT))

from app.core.config import settings  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.document_chunk import DocumentChunk  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from shared.db.database import engine  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

GENERIC_QUESTION_RE = re.compile(
    r"(这段|上述|该片段|本文|文档|这一部分).{0,6}(讲了什么|是什么|内容)"
)
SKIP_ANSWER_MARKERS = ("无法确定", "没有提到", "无法回答", "信息不足")

PROMPT = """你是企业知识库评测数据标注员。
下面有 {count} 个文档片段，每个片段带编号。请为每个片段生成 1 道题：

要求：
1. 问题必须具体，能由该片段直接回答；
2. 禁止“这段内容讲了什么/本文主要讲了什么”这类泛泛问题；
3. ground_truth 必须是简洁、完整、严格基于片段的答案，不得补充片段之外的信息；
4. 如果片段只是标题、目录、代码、乱码、重复内容，或信息不足以提问，
   返回 {{"id": 编号, "skip": true}}；
5. 必须为每个输入片段返回一项，编号与输入一致。

只输出 JSON：
{{"items": [
  {{"id": 1, "skip": false, "question": "...", "ground_truth": "..."}},
  {{"id": 2, "skip": true}}
]}}

文档片段：
{chunks}
"""


def _evenly_pick(items: list, count: int) -> list:
    if len(items) <= count:
        return items
    step = len(items) / count
    return [items[min(len(items) - 1, int(i * step))] for i in range(count)]


def load_chunks(user_id: int, per_doc: int) -> list[dict]:
    with Session(engine) as session:
        documents = session.exec(
            select(Document)
            .where(
                Document.user_id == user_id,
                Document.status == "completed",
                Document.deleted_at.is_(None),
            )
            .order_by(Document.id.asc())
        ).all()

        selected: list[dict] = []
        for document in documents:
            chunks = session.exec(
                select(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.user_id == user_id,
                )
                .order_by(DocumentChunk.chunk_index.asc())
            ).all()
            chunks = [
                chunk
                for chunk in chunks
                if len((chunk.content or "").strip()) >= 80
            ]
            for chunk in _evenly_pick(chunks, per_doc):
                selected.append({
                    "document_id": document.id,
                    "filename": document.filename,
                    # build_sources 对外展示的是存储文件名（UUID.pdf）。
                    "source_name": Path(document.file_path).name,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                })
        return selected


def _build_prompt(items: list[dict]) -> str:
    parts = []
    for index, item in enumerate(items, 1):
        parts.append(
            f"[片段 {index}] 文件：{item['filename']}，"
            f"chunk_index={item['chunk_index']}\n{item['content']}"
        )
    return PROMPT.format(count=len(items), chunks="\n\n".join(parts))


def _parse_payload(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _char_overlap(ground_truth: str, content: str) -> float:
    gt = {ch for ch in re.sub(r"\s+", "", ground_truth) if ch}
    src = {ch for ch in re.sub(r"\s+", "", content) if ch}
    if not gt:
        return 0.0
    return len(gt & src) / len(gt)


def _valid_item(item: dict, source: dict) -> bool:
    question = (item.get("question") or "").strip()
    ground_truth = (item.get("ground_truth") or "").strip()
    if not question or not ground_truth:
        return False
    if len(question) < 8 or len(question) > 200:
        return False
    if len(ground_truth) < 5 or len(ground_truth) > 500:
        return False
    if GENERIC_QUESTION_RE.search(question):
        return False
    if any(marker in ground_truth for marker in SKIP_ANSWER_MARKERS):
        return False
    if _char_overlap(ground_truth, source["content"]) < 0.3:
        return False
    return True


async def generate_batch(
    client: AsyncOpenAI,
    items: list[dict],
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    async with semaphore:
        messages = [
            {
                "role": "system",
                "content": "你只输出符合要求的 JSON，不要输出解释。",
            },
            {"role": "user", "content": _build_prompt(items)},
        ]
        last_error = None
        for attempt in range(2):
            try:
                response = await client.chat.completions.create(
                    model=settings.llm_model_id,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=6000,
                    response_format=(
                        {"type": "json_object"}
                        if attempt == 0
                        else None
                    ),
                )
                text = response.choices[0].message.content or ""
                data = _parse_payload(text)
                return data.get("items") or []
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        print(
            f"  [warn] 批次失败 chunks="
            f"{[item['chunk_index'] for item in items]}: "
            f"{type(last_error).__name__}: {last_error}"
        )
        return []


def _chunks_into_batches(items: list[dict], size: int) -> list[list[dict]]:
    return [
        items[start:start + size]
        for start in range(0, len(items), size)
    ]


async def generate(
    items: list[dict],
    batch_size: int,
    concurrency: int,
) -> list[dict]:
    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
    semaphore = asyncio.Semaphore(concurrency)
    batches = _chunks_into_batches(items, batch_size)
    results = await asyncio.gather(*(
        generate_batch(client, batch, semaphore)
        for batch in batches
    ))

    questions: list[dict] = []
    seen: set[str] = set()
    for batch, output_items in zip(batches, results):
        by_id = {
            item.get("id"): item
            for item in output_items
            if isinstance(item, dict)
        }
        for index, source in enumerate(batch, 1):
            item = by_id.get(index) or {}
            if item.get("skip"):
                continue
            if not _valid_item(item, source):
                continue
            question = item["question"].strip()
            key = " ".join(question.split()).casefold()
            if key in seen:
                continue
            seen.add(key)
            questions.append({
                "question": question,
                "ground_truth": item["ground_truth"].strip(),
                "expected_source": source["source_name"],
                "expected_filename": source["filename"],
                "source_document_id": source["document_id"],
                "source_chunk_index": source["chunk_index"],
            })
    return questions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从已索引文档生成 Ragas 评测题集",
    )
    parser.add_argument("--user-id", type=int, default=28)
    parser.add_argument("--per-doc", type=int, default=25)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印选中的 chunk，不调用 LLM",
    )
    args = parser.parse_args()

    items = load_chunks(args.user_id, args.per_doc)
    print(
        f"选中 chunks: {len(items)} "
        f"({args.per_doc}/文档, user_id={args.user_id})"
    )
    if args.dry_run:
        for item in items:
            print(
                f"  {item['filename']} "
                f"chunk={item['chunk_index']} "
                f"len={len(item['content'])}"
            )
        return

    questions = asyncio.run(generate(
        items,
        batch_size=args.batch_size,
        concurrency=args.concurrency,
    ))
    args.output.write_text(
        json.dumps(questions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"生成有效题目: {len(questions)} -> {args.output}")

    by_doc: dict[str, int] = {}
    for item in questions:
        by_doc[item["expected_filename"]] = (
            by_doc.get(item["expected_filename"], 0) + 1
        )
    for filename, count in sorted(by_doc.items()):
        print(f"  {filename}: {count}")


if __name__ == "__main__":
    main()
