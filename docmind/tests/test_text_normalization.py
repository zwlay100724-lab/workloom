"""文档入库前文本归一化的单元测试。"""
from app.rag import loaders
from app.rag.text_normalization import normalize_document_text
from langchain_core.documents import Document


def test_nfkc_normalizes_compatibility_ideographs():
    assert normalize_document_text("第⼀周") == "第一周"
    assert normalize_document_text("⽬标") == "目标"


def test_normalizes_cjk_radicals_supplement():
    # NFKC 不处理部首补充区（U+2E80–U+2EFF），用等价表转换。
    assert normalize_document_text("⻓期") == "长期"
    assert normalize_document_text("⻚⾯") == "页面"
    assert normalize_document_text("⻔⼝") == "门口"
    assert normalize_document_text("⻄⽅") == "西方"
    assert normalize_document_text("⻢") == "马"
    assert normalize_document_text("⻆⾊") == "角色"
    assert normalize_document_text("⻅") == "见"


def test_removes_spaces_between_cjk():
    assert normalize_document_text("你 好 世 界") == "你好世界"


def test_keeps_spaces_around_latin_and_numbers():
    text = "使用 Python 完成第 4 周任务"

    assert normalize_document_text(text) == text


def test_joins_wrapped_cjk_lines():
    text = "这是一段被 PDF 折行的很长中文文本，用于验证\n折行会被合并成一行。"

    assert normalize_document_text(text) == (
        "这是一段被 PDF 折行的很长中文文本,用于验证折行会被合并成一行。"
    )


def test_joins_wrapped_english_lines():
    text = (
        "This is a sufficiently long English sentence that wraps\n"
        "across two lines."
    )

    assert normalize_document_text(text) == (
        "This is a sufficiently long English sentence that wraps "
        "across two lines."
    )


def test_preserves_paragraph_breaks():
    text = "第一段内容。\n\n第二段内容。"

    assert normalize_document_text(text) == text


def test_collapses_extra_blank_lines():
    assert normalize_document_text("A。\n\n\n\nB。") == "A。\n\nB。"


def test_preserves_list_items():
    text = "1. 完成登录接口联调\n2. 补充 token 过期测试"

    assert normalize_document_text(text) == text


def test_preserves_short_headings():
    text = "阶段 1\nFastAPI 后端基础"

    assert normalize_document_text(text) == text


def test_removes_zero_width_and_control_chars():
    assert normalize_document_text("a​b\x01c") == "abc"


def test_mixed_ocr_sample():
    text = "不要做到⼀半突然：\n学习主线会直接断掉。"

    normalized = normalize_document_text(text)

    assert "⼀" not in normalized
    assert "一半" in normalized


def test_pdf_documents_are_normalized(monkeypatch):
    class FakePDFLoader:
        def __init__(self, path):
            self.path = path

        def load(self):
            return [Document(page_content="第⼀周 你 好")]

    monkeypatch.setattr(loaders, "PyPDFLoader", FakePDFLoader)

    documents = loaders.load_document("fake.pdf")

    assert documents[0].page_content == "第一周你好"
    assert documents[0].metadata["file_type"] == "pdf"


def test_text_documents_are_left_untouched(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("第⼀周 你 好", encoding="utf-8")

    documents = loaders.load_document(str(path))

    assert "第⼀周" in documents[0].page_content
