"""上传文档入库前的文本归一化。

PDF/DOCX 解析器经常产出：

- 兼容汉字（如 U+2F00 的 `⼀`，而不是标准 `一`）；
- 汉字之间被排版/OCR 插入的空格；
- 段落内部被 PDF 折行拆开的换行；
- 零宽字符和控制字符。

这些会直接破坏关键词、全文和 trigram 检索（例如查询 "第一周"
匹配不到文档里的 "第⼀周"），也会让引用片段读起来支离破碎。
这里在切分和 Embedding 之前统一清洗。

只对 PDF / DOCX 生效：Markdown / 纯文本可能包含代码块，不应改写。
"""
import re
import unicodedata

from app.rag.equivalent_ideographs import EQUIVALENT_UNIFIED_IDEOGRAPHS

# CJK 统一表意文字 + 扩展 A。NFKC 会把大多数兼容汉字映射回这里。
_CJK = "\u3400-\u4dbf\u4e00-\u9fff"
_CJK_RE = re.compile(f"[{_CJK}]")
# 同一行里，汉字之间的空格/不间断空格直接删掉。
_CJK_SPACE_RE = re.compile(f"(?<=[{_CJK}])[ \t\u00a0]+(?=[{_CJK}])")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_LIST_PREFIX_RE = re.compile(
    r"^\s*(?:[-*•·]|\d+[.)、]|[一二三四五六七八九十]+[、.)])"
)
# 以逗号/分号结尾的行一定还没说完，优先合并。
_CONTINUATION_END = (",", "，", "、", ";", "；")
_SENTENCE_END = tuple("。！？：.!?:")

# 很短的行更可能是标题/列表项，不参与折行合并。
_HEADING_MAX_CHARS = 16


def _normalize_line(line: str) -> str:
    """去掉一行内部汉字之间的空格。"""
    return _CJK_SPACE_RE.sub("", line).strip()


def _join_separator(previous: str, current: str) -> str | None:
    """判断两行是否属于同一个折行段落。

    返回 "" 表示直接拼接，返回 " " 表示用空格拼接，返回 None 表示保留换行。
    """
    if not previous or not current:
        return None

    current_char = current[0]
    current_is_cjk = bool(_CJK_RE.match(current_char))

    # 以逗号/分号结尾的行还没说完，即使很短也要并到下一行。
    if previous.endswith(_CONTINUATION_END):
        return "" if current_is_cjk else " "
    if previous.endswith(_SENTENCE_END):
        return None
    if _LIST_PREFIX_RE.match(current):
        return None
    if len(previous) <= _HEADING_MAX_CHARS:
        return None

    previous_char = previous[-1]
    previous_is_cjk = bool(_CJK_RE.match(previous_char))

    if previous_is_cjk and current_is_cjk:
        return ""
    if previous_char.isalnum() and current_char.isalnum():
        # 中文 + 英文/数字、英文 + 英文之间保留一个空格。
        return " "
    return None


def normalize_document_text(text: str) -> str:
    """归一化文档正文，供切分 / Embedding 使用。"""
    if not text:
        return ""

    # 1. 兼容汉字、全角字母数字等统一成标准形式。
    text = unicodedata.normalize("NFKC", text)
    # NFKC 不处理 CJK 部首补充区（如 ⻓/⻚/⻔），用 Unicode 等价表补上。
    text = text.translate(EQUIVALENT_UNIFIED_IDEOGRAPHS)

    # 2. 统一换行，去掉控制字符和零宽字符。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub("", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\t", " ").replace(" ", " ")

    # 3. 逐行去除汉字间空格，再合并 PDF 造成的折行。
    lines = [_normalize_line(line) for line in text.split("\n")]

    merged: list[str] = []
    for line in lines:
        if not line:
            # 最多保留一个空行，作为段落分隔。
            if merged and merged[-1] != "":
                merged.append("")
            continue

        if merged:
            separator = _join_separator(merged[-1], line)
            if separator is not None:
                merged[-1] = merged[-1] + separator + line
                continue

        merged.append(line)

    # 4. 压缩多余空行并去掉首尾空白。
    return re.sub(r"\n{3,}", "\n\n", "\n".join(merged)).strip()
