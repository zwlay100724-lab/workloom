"""
Chunk 策略实验：用同一批文档和问题，分别测试 chunk_size=300/500/800，
人工对比检索相关性（Top-3 中有几个是真正相关的）。
"""
from app.rag.loaders import load_document
from app.rag.splitters import split_documents

# 测试问题集（基于你上传的文档手动编写）
TEST_QUESTIONS = [
    "年假有多少天？",
    "病假需要什么证明？",
    "加班工资怎么算？",
    "试用期多长？",
    "离职提前多久通知？",
]


def run_experiment(pdf_path: str, user_id: int = 1):
    print("Chunk 策略对比实验")
    print("=" * 60)

    documents = load_document(pdf_path)

    for chunk_size in [300, 500, 800]:
        chunk_overlap = chunk_size // 5
        chunks = split_documents(documents, chunk_size, chunk_overlap)

        print(f"\n📊 chunk_size={chunk_size}, overlap={chunk_overlap}")
        print(f"   Chunk 总数: {len(chunks)}")

        # 注意：实际实验需要把不同 chunk_size 的向量分别存到不同的表/集合，
        # 或者用临时表。这里演示逻辑，实际操作时需要注意数据隔离。

        # 对每个问题检索，人工标注 Top-3 相关性
        print("\n   检索结果（请人工判断 Top-3 中有几个相关）:")
        for q in TEST_QUESTIONS:
            print(f"\n   Q: {q}")
            # 实际调用检索（需要先存入对应 chunk_size 的向量）
            # docs = retrieval.retrieve(q, user_id, top_k=3)
            # for i, d in enumerate(docs):
            #     print(f"     [{i+1}] {d.page_content[:80]}...")
            print("     (需存入向量后实际检索)")

    print("\n" + "=" * 60)
    print("实验记录模板：")
    print("| chunk_size | 问题1相关数 | 问题2相关数 | ... | 平均相关率 |")
    print("|------------|------------|------------|-----|-----------|")
    print("| 300        | ?/3        | ?/3        | ... | ?%        |")
    print("| 500        | ?/3        | ?/3        | ... | ?%        |")
    print("| 800        | ?/3        | ?/3        | ... | ?%        |")


if __name__ == "__main__":
    run_experiment("data/员工手册.pdf")