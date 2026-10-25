from langchain_core.tools import tool


def build_tools(retrieval_service):
    @tool
    def search_documents(query: str) -> str:
        """搜索公司知识库，返回回答问题所需的文档内容。"""
        documents = retrieval_service.search(query)
        return "\n\n".join(document.page_content for document in documents)

    return [search_documents]