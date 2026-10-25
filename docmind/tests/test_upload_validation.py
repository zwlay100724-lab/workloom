"""P0-06 回归：上传路径清洗、类型白名单、文件头校验。"""
import pytest
from app.api.documents import (
    detect_file_type,
    sanitize_filename,
    validate_signature,
)
from fastapi import HTTPException


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\x.pdf", "x.pdf"),
        ("/tmp/a.pdf", "a.pdf"),
        ("normal.PDF", "normal.PDF"),
        ("员工手册.pdf", "员工手册.pdf"),
        ("a\x00b.pdf", "ab.pdf"),
    ],
)
def test_sanitize_filename_strips_paths_and_control_chars(raw, expected):
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", "../", "..", "\\", "   "])
def test_sanitize_filename_rejects_empty_or_dot_only(raw):
    with pytest.raises(HTTPException) as exc_info:
        sanitize_filename(raw)
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.pdf", "pdf"),
        ("a.PDF", "pdf"),
        ("a.docx", "docx"),
        ("a.md", "text"),
        ("a.txt", "text"),
    ],
)
def test_detect_file_type(filename, expected):
    assert detect_file_type(filename) == expected


@pytest.mark.parametrize("filename", ["a.exe", "a", "a.tar.gz"])
def test_detect_file_type_rejects_unknown(filename):
    with pytest.raises(HTTPException) as exc_info:
        detect_file_type(filename)
    assert exc_info.value.status_code == 400


def test_validate_signature_accepts_matching_headers():
    validate_signature(b"%PDF-1.7", "pdf")
    validate_signature(b"PK\x03\x04", "docx")
    validate_signature(b"plain text", "text")


@pytest.mark.parametrize(
    ("head", "file_type"),
    [
        (b"<html>", "pdf"),
        (b"not a zip", "docx"),
    ],
)
def test_validate_signature_rejects_disguised_files(head, file_type):
    with pytest.raises(HTTPException) as exc_info:
        validate_signature(head, file_type)
    assert exc_info.value.status_code == 400
