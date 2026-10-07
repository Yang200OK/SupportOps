"""原文位置和解析错误是后续引用的基础契约。"""

import json
from io import BytesIO

import pytest
from pypdf import PdfWriter

from supportops.documents.parser import ParseFailure, parse_document


def test_req303_markdown_preserves_exact_lines_and_structure():
    source = (
        "# 配置说明\n\n中文 RD_TIMEOUT。\n\n"
        "| 参数 | 值 |\n| --- | --- |\n| timeout_ms | 2000 |\n\n"
        "```ini\ntimeout_ms=2000\n```\n"
    )
    result = parse_document(source.encode(), "md", "1.1")
    assert result.text == source
    assert {b.kind for b in result.blocks} >= {"heading", "paragraph", "table", "code"}
    lines = source.splitlines(keepends=True)
    for block in result.blocks:
        assert block.text == "".join(lines[block.line_start - 1 : block.line_end])
    heading = result.blocks[0]
    assert heading.heading_level == 1 and heading.line_start == 1


@pytest.mark.parametrize(
    "source,format,code",
    [
        (b"", "md", "EMPTY_DOCUMENT"),
        (b"\xff", "md", "INVALID_UTF8"),
        (b"not a pdf", "pdf", "INVALID_PDF"),
        (b"{broken", "json", "INVALID_CASE_JSON"),
    ],
)
def test_req303_parser_errors_are_explicit(source, format, code):
    with pytest.raises(ParseFailure) as failure:
        parse_document(source, format, "1.1")
    assert failure.value.code == code


def case_bytes(**extra):
    return json.dumps(
        {
            "schema_version": "1",
            "product": "relaydesk",
            "product_version": "1.1",
            "title": "投递超时报告",
            "description": "用户报告 RD_TIMEOUT，尚无根因结论。",
            "source_type": "synthetic_case",
            **extra,
        },
        ensure_ascii=False,
    ).encode()


def test_req303_json_has_field_locations_and_checks_version():
    result = parse_document(case_bytes(), "json", "1.1")
    assert any(b.json_pointer == "/description" and "RD_TIMEOUT" in b.text for b in result.blocks)
    with pytest.raises(ParseFailure, match="版本"):
        parse_document(case_bytes(), "json", "2.0")


@pytest.mark.parametrize("field", ["expected", "root_cause", "fault_label", "answer"])
def test_req301_case_contract_rejects_evaluation_labels(field):
    with pytest.raises(ParseFailure):
        parse_document(case_bytes(**{field: "hidden"}), "json", "1.1")


def test_req303_pdf_without_text_and_encrypted_are_not_success():
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    stream = BytesIO()
    writer.write(stream)
    with pytest.raises(ParseFailure) as failure:
        parse_document(stream.getvalue(), "pdf", "1.0")
    assert failure.value.code == "PDF_NO_TEXT"
    writer.encrypt("test-password")
    stream = BytesIO()
    writer.write(stream)
    with pytest.raises(ParseFailure) as failure:
        parse_document(stream.getvalue(), "pdf", "1.0")
    assert failure.value.code == "PDF_ENCRYPTED"
