"""片段的稳定身份：内容哈希。

同一个索引版本里，chunk 的 ``chunk_index`` 会随切分参数变化；
但“这段文本是什么”不依赖它在文档里的位置。用内容哈希作为
``chunk_uid``，重建索引后内容未变的片段仍然能被找到。

只去掉首尾空白，不做更强的归一化：
- 正文在入库前已经由 loaders / text_normalization 处理过；
- 对 Markdown / 代码块做过度的空白折叠会改变语义。
"""
import hashlib


def compute_chunk_uid(content: str) -> str:
    """返回片段内容的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
