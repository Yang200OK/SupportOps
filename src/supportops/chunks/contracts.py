"""切片长度以 Unicode 字符计量，引用位置采用左闭右开区间。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChunkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_chars: int = Field(default=600, ge=128, le=4000)
    overlap_chars: int = Field(default=80, ge=0)

    @model_validator(mode="after")
    def check_overlap(self):
        if self.overlap_chars * 2 >= self.max_chars:
            raise ValueError("重叠字符数必须小于目标长度的一半。")
        return self


class SourceSpan(BaseModel):
    start: int
    end: int
    line_start: int | None = None
    line_end: int | None = None


class ParentChunk(BaseModel):
    parent_id: UUID
    heading_path: list[str]
    start: int
    end: int
    text: str
    page_number: int | None = None
    json_pointer: str | None = None


class ChildChunk(BaseModel):
    chunk_id: UUID
    parent_id: UUID
    ordinal: int
    kind: Literal["heading", "prose", "table", "code", "page", "field"]
    heading_path: list[str]
    text: str
    spans: list[SourceSpan]
    page_number: int | None = None
    json_pointer: str | None = None
    table_headers: list[str] = Field(default_factory=list)
    code_language: str | None = None
    atomic_oversize: bool = False


class ChunkResult(BaseModel):
    chunk_set_id: UUID
    revision_id: UUID
    chunker_version: Literal["structure-v1"] = "structure-v1"
    config: ChunkConfig
    config_sha256: str
    parsed_sha256: str
    parents: list[ParentChunk]
    chunks: list[ChildChunk]
    snapshot_sha256: str = ""


class ChunkSetView(BaseModel):
    chunk_set_id: UUID
    document_id: UUID
    revision_id: UUID
    product_version: str
    revision_number: int
    content_sha256: str
    parsed_sha256: str
    chunker_version: str
    config: ChunkConfig
    config_sha256: str
    snapshot_sha256: str
    parent_count: int
    chunk_count: int
    created_at: datetime
    reused: bool = False


class ChunkSetList(BaseModel):
    items: list[ChunkSetView]
    total: int
    offset: int
    limit: int


class ChunkPage(BaseModel):
    items: list[ChildChunk]
    total: int
    offset: int
    limit: int
    snapshot: ChunkSetView
