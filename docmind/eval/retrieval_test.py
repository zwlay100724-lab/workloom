"""
检索效果测试：问几个问题，人工检查返回的片段是否相关。
同时测试不同 Top-K 的差异。
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

# eval 脚本不走 app.main()，main.py 顶部的 load_dotenv() 不会执行；
# 而 shared.db.database 在 import 时直接读 os.environ["DATABASE_URL"]，
# 所以必须先手动加载 docmind/.env，否则报 "DATABASE_URL is not configured"。
# 用相对 __file__ 的路径，不依赖 cwd（PyCharm 右键跑时 cwd 可能是 eval/）。
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 允许直接 `python eval/xxx.py` 运行：脚本模式时 sys.path[0] 是 eval/，
# 手动把项目根加回 sys.path（模块模式 python -m 时无副作用）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.retrieval_service import RetrievalService
from shared.db.database import get_session


def main():
    session = next(get_session())
    retrieval = RetrievalService()

    # 换成你上传的文档里有的问题
    questions = [
        "板块一的标题是什么？",
        "板块二的标题是什么？",
        "板块三是什么内容？",
    ]

    print("=" * 60)
    print("检索效果测试（Top-K=3）")
    print("=" * 60)

    for question in questions:
        print(f"\n\n❓ 问题: {question}")
        print("-" * 40)

        docs = retrieval.retrieve(
            session,
            question=question,
            user_id=1,  # 换成你的用户ID
            top_k=3,
        )

        for i, doc in enumerate(docs):
            page = doc.metadata.get("page")
            page_str = f"第{page + 1}页" if page is not None else "?"
            sim = doc.metadata.get("similarity", 0)
            print(f"\n  【{i + 1}】{doc.metadata.get('source')} {page_str} (相似度: {sim:.3f})")
            print(f"  {doc.page_content[:150]}...")

    # Top-K 对比实验
    print("\n\n" + "=" * 60)
    print("Top-K 对比实验（同一个问题，K=2/5/10）")
    print("=" * 60)

    question = questions[0]
    for k in [2, 5, 10]:
        docs = retrieval.retrieve(session, question=question, user_id=1, top_k=k)
        print(f"\nK={k}: 返回 {len(docs)} 个片段")
        for i, doc in enumerate(docs):
            sim = doc.metadata.get("similarity", 0)
            print(f"  {i + 1}. 相似度={sim:.3f} | {doc.page_content[:60]}...")


if __name__ == "__main__":
    main()