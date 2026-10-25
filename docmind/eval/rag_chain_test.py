"""
RAG 全链路测试：问题 → 向量化 → 检索 Top-K → 拼 Prompt → LLM 生成 → 来源标注。

主要看两件事：
1. 链路是否通：LLM 能不能正常调用、有没有报错；
2. 效果对不对：回答是否基于检索到的片段，检索不到时是否按 Prompt 要求答"无法回答"。

注意：每个问题都会真实调用一次 LLM（消耗 API 额度 + 耗时），别一次塞太多问题。

运行（在 docmind/ 下，用项目 venv）：
    ../.venv/bin/python eval/rag_chain_test.py
"""
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# eval 脚本不走 app.main()，main.py 顶部的 load_dotenv() 不会执行；
# 而 shared.db.database / LLM 配置在 import 时直接读环境变量，
# 所以必须先手动加载 docmind/.env。用相对 __file__ 的路径，不依赖 cwd。
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 允许直接 `python eval/xxx.py` 运行：脚本模式时 sys.path[0] 是 eval/，
# 手动把项目根加回 sys.path（模块模式 python -m 时无副作用）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# shared/db/database.py 硬编码 echo=True，会把每条 SQL 打到日志上刷屏；
# 设 logger 级别压不住它，直接关掉 engine 的 echo，让输出干净。
from shared.db.database import engine, get_session  # noqa: E402

engine.echo = False  # noqa: E402

from app.services.rag_service import RAGService  # noqa: E402

USER_ID = 1  # 换成你的用户ID（本版无鉴权，固定占位）
TOP_K = 3


def ask(rag: RAGService, question: str) -> dict:
    """跑一次完整 RAG 问答，返回结果 dict（含 answer / sources / context）。"""
    return rag.query(
        question=question,
        user_id=USER_ID,
        top_k=TOP_K,
    )


def print_sources(result: dict) -> None:
    sources = result.get("sources", [])
    if not sources:
        print("   （没有检索到来源片段）")
        return
    for i, src in enumerate(sources):
        page = src.get("page_display") or "?"
        sim = src.get("similarity", 0)
        print(
            f"   [{i + 1}] {src.get('document')} {page} "
            f"(相似度 {sim:.3f})"
        )
        print(f"       {src.get('snippet', '')}")


def main():
    session = next(get_session())
    rag = RAGService(session)

    # 换成你的文档里真实会问的问题。
    # 最后一个故意不在资料里，用来验证"检索不到时 LLM 不乱编"。
    questions = [
        "板块三讲什么？",
        "什么是 Retriever？它和直接查数据库有什么区别？",
        "为什么需要文本分块？",
        "今天晚上吃什么？",  # 资料里没有 → 应回答"无法回答"
    ]

    print("=" * 64)
    print(f"RAG 全链路测试（{len(questions)} 题，Top-K={TOP_K}，user_id={USER_ID}）")
    print("每问一次 → 向量化 + 检索 + LLM 生成，注意会消耗 LLM API")
    print("=" * 64)

    for n, question in enumerate(questions, 1):
        print(f"\n\n{'─' * 64}")
        print(f"Q{n}. {question}")
        print("─" * 64)

        # 先打印检索到的片段（LLM 的依据），再打印回答，方便对照有没有乱编
        start = time.time()
        try:
            result = ask(rag, question)
        except Exception as e:
            print(f"  ❌ 调用失败: {type(e).__name__}: {e}")
            continue
        elapsed = time.time() - start

        print("\n  📥 检索到的片段：")
        print_sources(result)

        print(f"\n  🤖 LLM 回答（{elapsed:.1f}s）：")
        for line in result["answer"].splitlines():
            print(f"    {line}")

    print("\n\n" + "=" * 64)
    print("测试结束。检查点：")
    print("1. 每道题都正常出回答、没有报错 → 链路通")
    print("2. 回答内容是否和上面的片段对得上 → 有没有乱编")
    print("3. 最后那道题（资料外的）是否回答\"无法回答\" → 约束是否生效")
    print("=" * 64)


if __name__ == "__main__":
    main()
