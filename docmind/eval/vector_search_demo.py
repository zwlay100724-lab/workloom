"""
验证向量存储和检索：
1. 确认 document_chunks 表中有数据
2. 对一个问题向量化，做相似度检索
3. 检查返回的片段是否相关
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

from app.rag.embeddings import get_embeddings
from app.repositories.vector_repository import VectorRepository
from shared.db.database import get_session


def main():
    session = next(get_session())
    vector_repo = VectorRepository(session)

    # 1. 查一下有多少条向量
    from app.models.document_chunk import DocumentChunk
    from sqlmodel import func, select
    count = session.exec(select(func.count(DocumentChunk.id))).one()
    print(f"📊 document_chunks 表中共有 {count} 条向量记录")

    if count == 0:
        print("⚠️ 没有数据，请先上传一个 PDF")
        return

    # 2. 对问题向量化
    question = "板块二是什么内容？"  # 换成你上传的文档里有的问题
    embeddings = get_embeddings()
    query_vec = embeddings.embed_query(question)
    print(f"\n❓ 问题: {question}")
    print(f"   向量维度: {len(query_vec)}")

    # 3. 相似度检索（假设 user_id=1，换成你的实际用户ID）
    results = vector_repo.search_similar(
        query_embedding=query_vec,
        user_id=1,
        top_k=3,
    )

    # 4. 打印结果
    print(f"\n🔍 检索到 Top-{len(results)} 个相关片段:\n")
    for i, (chunk, distance) in enumerate(results):
        similarity = 1 - distance  # 余弦距离转相似度
        print(f"--- 排名 {i + 1} (相似度: {similarity:.4f}, 距离: {distance:.4f}) ---")
        print(f"来源: {chunk.source} 第{chunk.page + 1 if chunk.page is not None else '?'}页")
        print(f"内容: {chunk.content[:200]}...")
        print()


if __name__ == "__main__":
    main()