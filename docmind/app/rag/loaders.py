import os

from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, TextLoader
from langchain_core.documents import Document


def load_document(file_path: str) -> list[Document]:
    """
    根据文件扩展名自动选择加载器，统一返回 List[Document]。
    支持 PDF / Word / Markdown / 纯文本。
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        loader = PyPDFLoader(file_path)
        file_type = "pdf"
    elif ext == ".docx":
        loader = Docx2txtLoader(file_path)
        file_type = "docx"
    elif ext in (".md", ".txt"):
        loader = TextLoader(file_path)
        file_type = "text"
    else:
        raise ValueError(f"不支持的文件格式: {ext}")

    documents = loader.load()

    # 统一补充 metadata
    for doc in documents:
        doc.metadata["file_type"] = file_type
        # TextLoader 没有 page 字段，补一个默认值
        if "page" not in doc.metadata:
            doc.metadata["page"] = 0

    return documents


# 保留原有的 load_pdf 作为别名（兼容旧代码）
def load_pdf(file_path: str) -> list[Document]:
    return load_document(file_path)