"""
Embedding 语义相似度验证：
对 3 个句子做向量化，计算它们之间的余弦相似度，
验证"语义相近的句子相似度高"。
"""
import numpy as np
from app.rag.embeddings import get_embeddings


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度"""
    a = np.array(a)
    b = np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main():
    embeddings = get_embeddings()

    sentences = [
        "如何申请年假？",
        "年假的申请流程是什么？",
        "今天天气怎么样？",
    ]

    # 批量向量化
    vectors = embeddings.embed_documents(sentences)

    print(f"向量维度: {len(vectors[0])}")
    print(f"第一个向量前5个值: {vectors[0][:5]}")
    print("\n" + "=" * 50)

    # 计算两两相似度
    for i in range(len(sentences)):
        for j in range(i + 1, len(sentences)):
            sim = cosine_similarity(vectors[i], vectors[j])
            print(f"\n「{sentences[i]}」 vs 「{sentences[j]}」")
            print(f"  余弦相似度: {sim:.4f}")

    print("\n" + "=" * 50)
    print("\n预期：前两句相似度 > 0.8（语义相近）")
    print("     第一句和第三句相似度 < 0.3（语义无关）")


if __name__ == "__main__":
    main()