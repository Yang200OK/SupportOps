"""解析只记录原文结构与位置，不生成答案或检索切片。"""

import json
from io import BytesIO
from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from supportops.documents.contracts import MAX_BYTES, ParsedBlock, ParsedDocument, Version

MAX_TEXT = 200_000


class ParseFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CaseDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    schema_version: Literal["1"]
    product: Literal["relaydesk"]
    product_version: Version
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    source_type: Literal["synthetic_case", "user_report"]


def utf8(raw: bytes) -> str:
    try:
        result = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ParseFailure("INVALID_UTF8", "原文不是合法 UTF-8。") from None
    if "\x00" in result or "\ufffd" in result:
        raise ParseFailure("INVALID_TEXT", "原文包含空字符或替换字符，请修正编码。")
    return result


def markdown(source: str) -> ParsedDocument:
    lines = source.splitlines(keepends=True)
    blocks = []
    tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(source)
    kinds = {
        "heading_open": "heading",
        "paragraph_open": "paragraph",
        "table_open": "table",
        "fence": "code",
        "code_block": "code",
    }
    covered = -1
    for token in tokens:
        if token.map is None or token.type not in kinds:
            continue
        start, end = token.map
        # 配置表的内部单元格已经由表格原文覆盖，不再次创建重复块。
        if start < covered:
            continue
        blocks.append(
            ParsedBlock(
                kind=kinds[token.type],
                text="".join(lines[start:end]),
                line_start=start + 1,
                line_end=end,
                heading_level=int(token.tag[1:]) if token.type == "heading_open" else None,
            )
        )
        covered = end
    if not blocks:
        raise ParseFailure("EMPTY_DOCUMENT", "文档没有可解析正文。")
    return ParsedDocument(text=source, blocks=blocks)


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复 JSON 字段")
        result[key] = value
    return result


def case_json(source: str, version: Version) -> ParsedDocument:
    try:
        data = json.loads(source, object_pairs_hook=no_duplicate_keys)
        case = CaseDocument.model_validate(data)
    except (ValueError, ValidationError):
        raise ParseFailure(
            "INVALID_CASE_JSON", "案例 JSON 格式不符合无答案标签的资料契约。"
        ) from None
    if case.product_version != version:
        raise ParseFailure("VERSION_MISMATCH", "案例正文版本与导入版本不一致。")
    blocks = [
        ParsedBlock(kind="field", text=getattr(case, name), json_pointer="/" + name)
        for name in ("title", "description")
    ]
    return ParsedDocument(text="\n\n".join(b.text for b in blocks), blocks=blocks)


def pdf(raw: bytes) -> ParsedDocument:
    try:
        reader = PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise ParseFailure("PDF_ENCRYPTED", "不接受加密 PDF，请提供未加密的文本文件。")
        if not 1 <= len(reader.pages) <= 40:
            raise ParseFailure("PDF_PAGE_LIMIT", "PDF 必须包含 1 至 40 页。")
        blocks = []
        total = 0
        for number, page in enumerate(reader.pages, 1):
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > 4 * 1024 * 1024:
                raise ParseFailure("PDF_STREAM_LIMIT", "PDF 页面解压内容超过本轮解析上限。")
            content = page.extract_text()
            if not content or not content.strip():
                raise ParseFailure("PDF_NO_TEXT", "PDF 存在无法提取文本的页面；本轮不执行 OCR。")
            if "\ufffd" in content or "\x00" in content:
                raise ParseFailure("INVALID_TEXT", "PDF 提取结果包含异常字符。")
            total += len(content)
            if total > MAX_TEXT:
                raise ParseFailure("TEXT_LIMIT", "提取正文超过 200000 字符。")
            blocks.append(ParsedBlock(kind="page", text=content, page_number=number))
        return ParsedDocument(text="\n\n".join(b.text for b in blocks), blocks=blocks)
    except ParseFailure:
        raise
    except (PyPdfError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        raise ParseFailure("INVALID_PDF", "PDF 损坏或结构不受支持，未生成可用正文。") from None


def parse_document(raw: bytes, format: str, version: Version) -> ParsedDocument:
    if len(raw) > MAX_BYTES:
        raise ParseFailure("FILE_LIMIT", "原文超过 2 MiB。")
    if not raw or not raw.strip():
        raise ParseFailure("EMPTY_DOCUMENT", "原文不能为空。")
    if format == "pdf":
        return pdf(raw)
    source = utf8(raw)
    if len(source) > MAX_TEXT:
        raise ParseFailure("TEXT_LIMIT", "正文超过 200000 字符。")
    if format == "md":
        return markdown(source)
    if format == "json":
        return case_json(source, version)
    raise ParseFailure("UNSUPPORTED_FORMAT", "不支持该原文格式。")
