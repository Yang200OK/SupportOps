"""资料输入不接受客户端组织、修订编号或解析成功声明。"""

import base64
import binascii
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 3 * 1024 * 1024
Version = Literal["1.0", "1.1", "2.0"]
Format = Literal["md", "pdf", "json"]


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    source_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str = Field(min_length=1, max_length=200)
    product: Literal["relaydesk"]
    product_version: Version
    source_type: Literal["demo_product", "synthetic_case", "user_report"]
    license: Literal["CC0-1.0", "proprietary"]
    filename: str = Field(min_length=1, max_length=150)
    format: Format
    content_base64: str = Field(max_length=4 * ((MAX_BYTES + 2) // 3))

    @model_validator(mode="after")
    def check_file(self):
        # 简单文件名只作显示与下载；Windows 路径、控制字符不进入响应头。
        if re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', self.filename):
            raise ValueError("文件名必须是简单文件名。")
        if self.filename.endswith((".", " ")) or not self.filename.lower().endswith(
            "." + self.format
        ):
            raise ValueError("文件名后缀与格式不一致。")
        try:
            raw = base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("文件内容必须为合法 Base64。") from None
        if len(raw) > MAX_BYTES:
            raise ValueError("原文超过 2 MiB。")
        return self

    def raw_bytes(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


class ParsedBlock(BaseModel):
    kind: Literal["heading", "paragraph", "table", "code", "page", "field", "other"]
    text: str
    line_start: int | None = None
    line_end: int | None = None
    heading_level: int | None = None
    page_number: int | None = None
    json_pointer: str | None = None


class ParsedDocument(BaseModel):
    parser_version: Literal["source-parser-v1"] = "source-parser-v1"
    text: str
    blocks: list[ParsedBlock]


class DocumentSummary(BaseModel):
    document_id: UUID
    revision_id: UUID
    revision_number: int
    source_key: str
    title: str
    product: str
    product_version: Version
    source_type: str
    license: str
    filename: str
    format: Format
    content_sha256: str
    byte_size: int
    imported_at: datetime
    importer_id: UUID
    status: Literal["parsed", "failed"]
    error_code: str | None
    error_message: str | None
    parser_version: str


class DocumentView(DocumentSummary):
    text: str | None
    blocks: list[ParsedBlock]
    reused: bool = False


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    total: int
    offset: int
    limit: int
