"""结构切片必须保留确定位置、版本身份与不可切断的结构。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from supportops.chunks.chunking import ChunkFailure, build_chunks
from supportops.chunks.contracts import ChunkConfig
from supportops.documents.contracts import ParsedBlock, ParsedDocument
from supportops.documents.parser import parse_document


def build(source, format="md", config=None, revision=None):
    parsed = parse_document(source.encode(), format, "1.1")
    return build_chunks(revision or uuid4(), parsed, format, config or ChunkConfig())


def assert_locations(result, source):
    for chunk in result.chunks:
        assert chunk.text == "".join(source[s.start : s.end] for s in chunk.spans)
        parent = next(p for p in result.parents if p.parent_id == chunk.parent_id)
        assert parent.text == source[parent.start : parent.end]
        assert all(parent.start <= s.start < s.end <= parent.end for s in chunk.spans)


def test_req401_ids_and_digest_are_deterministic_and_config_sensitive():
    revision = uuid4()
    source = "# 配置\n\n中文 RD_TIMEOUT。\n"
    one = build(source, revision=revision)
    same = build(source, revision=revision)
    assert one == same
    assert one.chunk_set_id != build(source).chunk_set_id
    assert (
        one.chunk_set_id
        != build(
            source, config=ChunkConfig(max_chars=128, overlap_chars=20), revision=revision
        ).chunk_set_id
    )
    assert_locations(one, source)


def test_req402_sections_nested_headings_and_long_prose():
    source = (
        "# 产品\n\n前言。\n\n## 配置\n\n"
        + "中文错误码 RD_TIMEOUT。" * 40
        + "\n\n### 单位\n\n毫秒。\n\n## 缓存\n\n代次。\n"
    )
    result = build(source, config=ChunkConfig(max_chars=128, overlap_chars=20))
    assert any(c.heading_path == ["产品", "配置", "单位"] for c in result.chunks)
    assert any(c.heading_path == ["产品", "缓存"] for c in result.chunks)
    assert all(len(c.text) <= 128 for c in result.chunks)
    prose = [c for c in result.chunks if c.heading_path == ["产品", "配置"] and c.kind == "prose"]
    assert len(prose) > 1
    assert prose[0].spans[-1].end - prose[1].spans[0].start == 20
    assert_locations(result, source)


def test_req403_long_table_repeats_exact_header_without_splitting_rows():
    header = "| 参数 | 值 |\n| --- | --- |\n"
    rows = [f"| timeout_{i} | {2000 + i} |\n" for i in range(20)]
    source = "# 配置\n\n" + header + "".join(rows)
    result = build(source, config=ChunkConfig(max_chars=128, overlap_chars=20))
    tables = [c for c in result.chunks if c.kind == "table"]
    assert len(tables) > 1
    for chunk in tables:
        assert chunk.text.startswith(header)
        assert chunk.table_headers == ["参数", "值"]
        assert all(line + "\n" in rows for line in chunk.text.splitlines()[2:])
    assert_locations(result, source)


def test_req403_long_code_preserves_fences_language_and_complete_lines():
    body = [f"timeout_{i}=2000\n" for i in range(24)]
    source = "# 配置\n\n```ini\n" + "".join(body) + "```\n"
    result = build(source, config=ChunkConfig(max_chars=128, overlap_chars=20))
    codes = [c for c in result.chunks if c.kind == "code"]
    assert len(codes) > 1
    for chunk in codes:
        assert chunk.code_language == "ini"
        assert chunk.text.startswith("```ini\n") and chunk.text.endswith("```\n")
        assert all(line + "\n" in body for line in chunk.text.splitlines()[1:-1])
    assert_locations(result, source)


def test_req403_oversized_atomic_line_is_explicit_not_truncated():
    source = "# 代码\n\n```text\n" + "x" * 200 + "\n```\n"
    result = build(source, config=ChunkConfig(max_chars=128, overlap_chars=20))
    assert any(c.atomic_oversize for c in result.chunks)
    with pytest.raises(ChunkFailure, match="8000"):
        build("```text\n" + "x" * 8001 + "\n```\n")


def test_req404_json_pointers_and_decoded_offsets():
    source = (
        '{"schema_version":"1","product":"relaydesk","product_version":"1.1",'
        '"title":"中文标题","description":"RD_TIMEOUT\\n报告",'
        '"source_type":"synthetic_case"}'
    )
    parsed = parse_document(source.encode(), "json", "1.1")
    result = build_chunks(uuid4(), parsed, "json", ChunkConfig())
    assert {c.json_pointer for c in result.chunks} == {"/title", "/description"}
    assert_locations(result, parsed.text)


@pytest.mark.parametrize(
    "data",
    [
        {"max_chars": 127},
        {"overlap_chars": 300},
        {"organization_id": "forged"},
        {"max_chars": "600"},
    ],
)
def test_req401_bad_configuration_is_rejected(data):
    with pytest.raises(ValidationError):
        ChunkConfig.model_validate(data)


def test_req404_pdf_offsets_refer_to_extracted_page_text():
    pages = ["第一页 RD_TIMEOUT", "第二页配置说明"]
    parsed = ParsedDocument(
        text="\n\n".join(pages),
        blocks=[
            ParsedBlock(kind="page", text=value, page_number=i + 1) for i, value in enumerate(pages)
        ],
    )
    result = build_chunks(uuid4(), parsed, "pdf", ChunkConfig())
    assert [c.page_number for c in result.chunks] == [1, 2]
    assert all(s.line_start is None for c in result.chunks for s in c.spans)
    assert_locations(result, parsed.text)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_req402_setext_newlines_and_short_paragraph_merge(newline):
    source = newline.join(["配置说明", "========", "", "第一段。", "", "第二段。", ""])
    result = build(source)
    prose = [c for c in result.chunks if c.kind == "prose"]
    assert len(prose) == 1
    assert prose[0].heading_path == ["配置说明"]
    assert prose[0].text == newline.join(["第一段。", "", "第二段。", ""])
    assert prose[0].spans[0].line_start == 4
    assert prose[0].spans[0].line_end == 6
    assert_locations(result, source)


@pytest.mark.parametrize("source", ["    timeout=2000\n", "~~~ini\ntimeout=2000\n"])
def test_req403_original_indentation_and_unclosed_fence_are_preserved(source):
    result = build(source)
    assert result.chunks[0].text == source
    assert_locations(result, source)


def test_req404_invalid_parser_locations_fail_without_reparse():
    parsed = parse_document("# 配置\n\n正文。\n".encode(), "md", "1.1")
    parsed.blocks[1].text = "伪造"
    with pytest.raises(ChunkFailure, match="位置"):
        build_chunks(uuid4(), parsed, "md", ChunkConfig())


def test_req406_parent_budget_is_enforced():
    with pytest.raises(ChunkFailure, match="3000"):
        build("# Heading\n" * 3001)


@pytest.mark.parametrize(
    "opening,body_prefix,closing",
    [
        ("> ```ini\n", "> ", "> ```\n"),
        ("- ```ini\n", "  ", "  ```\n"),
    ],
)
def test_req403_nested_fences_preserve_original_container_lines(opening, body_prefix, closing):
    source = opening + "".join(f"{body_prefix}timeout_{i}=2000\n" for i in range(20)) + closing
    result = build(source, config=ChunkConfig(max_chars=128, overlap_chars=20))
    assert len(result.chunks) > 1
    assert all(c.text.startswith(opening) and c.text.endswith(closing) for c in result.chunks)
    assert_locations(result, source)
