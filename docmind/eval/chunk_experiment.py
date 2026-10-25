"""
Chunk Size 对比实验：
用同一个 PDF，分别测试 chunk_size=300/500/800，观察分割结果差异。
"""
import sys
from pathlib import Path

# 允许直接 `python eval/chunk_experiment.py` 运行：
# 脚本模式时 sys.path[0] 是 eval/ 而不是项目根，导致 import app.* 失败，
# 这里把项目根目录手动加回 sys.path（模块模式 python -m 时无副作用）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.loaders import load_pdf
from app.rag.splitters import split_documents


def run_experiment(pdf_path: str):
    documents = load_pdf(pdf_path)
    print(f"文档总页数: {len(documents)}")
    print(f"文档总字符数: {sum(len(d.page_content) for d in documents)}")
    print("=" * 60)

    for chunk_size in [300, 500, 800]:
        chunk_overlap = chunk_size // 5  # overlap 取 size 的 20%
        chunks = split_documents(documents, chunk_size, chunk_overlap)

        # 统计
        lengths = [len(c.page_content) for c in chunks]
        avg_length = sum(lengths) / len(lengths)
        max_length = max(lengths)
        min_length = min(lengths)

        print(f"\n📊 chunk_size={chunk_size}, overlap={chunk_overlap}")
        print(f"   Chunk 总数: {len(chunks)}")
        print(f"   平均长度: {avg_length:.0f} 字符")
        print(f"   最大长度: {max_length} 字符")
        print(f"   最小长度: {min_length} 字符")

        # 看第一个 Chunk 的内容
        print(f"\n   第一个 Chunk 前100字: {chunks[0].page_content[:100]}...")
        print("-" * 60)


if __name__ == "__main__":
    run_experiment("data/1_1788405623_第四阶段RAG-豆包.pdf")