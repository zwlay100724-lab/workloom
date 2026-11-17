"""查询侧关键词抽取。

hybrid 的关键词一路需要的是“检索词”，不是整句。长自然语言问题
（例如“第一周目标是什么”）整句做子串/trigram 匹配几乎不可能命中，
所以先用 jieba 的 TF-IDF 抽名词、动词和英文术语，再交给
VectorRepository 做关键词召回。

抽取失败或全部被停用词过滤时回退为整句，保证不会比原来的行为更差。
"""
import logging

import jieba
import jieba.analyse

# jieba 默认会把词典加载日志打到 stderr，压低到 warning。
jieba.setLogLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# 允许的词性：名词、专名、动名词、动词、英文、数词/量词。
_ALLOWED_POS = ("n", "ns", "nz", "vn", "v", "eng", "m")
_MAX_KEYWORDS = 8

_STOPWORDS = frozenset({
    # 疑问 / 指代 / 泛化词
    "什么", "怎么", "怎样", "如何", "哪些", "哪个", "为什么", "多少",
    "是否", "有没有", "能不能", "可不可以", "这个", "那个", "这些",
    "那些", "目前", "现在", "今天", "明天", "最近", "时候", "事情",
    "东西", "方面", "情况", "内容", "相关", "有关", "一下", "一些",
    "应该", "可以", "需要", "帮我", "帮忙", "请问", "告诉",
    # 英文常见停用词
    "the", "and", "or", "of", "to", "in", "on", "for", "with", "what",
    "how", "which", "is", "are",
})

# 领域词，避免被 jieba 拆碎（例如“截止日期”被拆成“截止”+“时候”）。
_DOMAIN_WORDS = (
    "第一周", "第二周", "第三周", "第四周", "截止日期", "交付物",
    "验收标准", "责任人", "负责人", "里程碑", "行动项", "待办事项",
    "知识库", "向量检索", "混合检索", "语义检索", "全文检索",
    "关键词检索", "重索引", "索引版本", "任务生成", "文档生成",
    "关联文档", "引用片段", "TaskFlow", "DocMind", "WorkLoom",
    "pgvector", "LangGraph", "LangChain", "MCP",
)
for _word in _DOMAIN_WORDS:
    # 显式给 tag：否则 add_word 默认标成 'x'，会被 allowPOS 过滤掉。
    jieba.add_word(_word, freq=10000, tag="n")


def extract_keywords(query: str, top_k: int = _MAX_KEYWORDS) -> list[str]:
    """从用户问题中抽取用于关键词召回的词。

    返回空列表表示 query 为空；抽取失败或全被过滤时回退为整句。
    """
    query = (query or "").strip()
    if not query:
        return []

    try:
        tags = jieba.analyse.extract_tags(
            query,
            topK=top_k * 2,
            withWeight=False,
            allowPOS=_ALLOWED_POS,
        )
    except Exception:  # noqa: BLE001 — 关键词抽取失败不能阻塞检索
        logger.exception("keyword extraction failed query=%r", query)
        return [query]

    keywords: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        keyword = raw.strip()
        if not keyword:
            continue
        key = keyword.lower()
        if key in _STOPWORDS or key in seen:
            continue
        # 中文单字噪声太大；英文/数字术语允许两个字符。
        if len(keyword) < 2 and not keyword.isascii():
            continue
        seen.add(key)
        keywords.append(keyword)
        if len(keywords) >= top_k:
            break

    return keywords or [query]
