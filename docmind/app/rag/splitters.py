from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings


def split_documents(
        documents: list[Document],
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
) -> list[Document]:
    """
    将 Document 列表切分成更小的 Chunk。

    Args:
        documents: 板块二加载的 Document 列表（每页一个）
        chunk_size: 每个片段最大字符数；None 时读 settings.CHUNK_SIZE
        chunk_overlap: 相邻片段重叠字符数；None 时读 settings.CHUNK_OVERLAP

    Returns:
        list[Document]: 切分后的片段列表，每个 Chunk 继承原 metadata，
                        并新增 chunk_index 字段

    为什么默认值不再写死 500/100：
    生产调用（DocumentService.process_pdf）不传参，字面量默认值会让
    .env 里的 CHUNK_SIZE / CHUNK_OVERLAP 永远不生效，评测实验的结论
    也会和线上实际切分参数不一致。现在“不传”明确表示“用配置”。
    """
    if chunk_size is None:
        chunk_size = settings.CHUNK_SIZE
    if chunk_overlap is None:
        chunk_overlap = settings.CHUNK_OVERLAP

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        # 分隔符优先级：段落 → 行 → 中文句号 → 英文句号 → 空格 → 字符
        separators=["\n\n", "\n", "。", ".", " ", ""],
    )

    chunks = splitter.split_documents(documents)

    # 给每个 chunk 补充全局序号（跨页的连续编号）
    for i, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = i
        # 原有的 source、page、file_type 会自动继承，不需要手动加

    return chunks